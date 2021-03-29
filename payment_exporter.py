#!/usr/bin/python3
import requests
import logging
import argparse
import json
import signal
import threading
from time import sleep
import dateutil.parser as dp
from prometheus_client import start_http_server, Gauge


class Extractor:

    def __init__(self, logger, username, password, base_url):
        self.log = logger
        self.session = requests.Session()

        self.username = username
        self.password = password

        self.xsrf_url = base_url + '/favicon.ico'
        self.auth_url = base_url + '/auth'
        self.contract_url = base_url + '/api/user/contract'
        self.subscriber_url = base_url + '/api/user/subscriber'
        self.product_url = base_url + '/api/user/product'
        self.data_url = base_url + '/api/user/all'
        self.bonus_url = base_url + '/api/gbonus/info'

        self.default_headers = {
            'Accept': 'application/json, text/plain, */*',
            'Connection': 'keep-alive',
        }

    def make_request(self, method, url, headers=None, **kwargs):
        s = self.session
        if headers is None:
            headers = self.default_headers
        try:
            response = s.request(method, url, headers=headers, **kwargs)
            self.log.debug('Session cookies: {}'.format(s.cookies.get_dict()))
        except Exception as err:
            self.log.error('Error: {}'.format(err))
            return None
        if not response.ok:
            self.log.error('4xx or 5xx response: {}'.format(response.text))
        return response

    def parse_response(self, response):
        if not response:
            return None
        try:
            data = response.json()
        except Exception as err:
            self.log.error('Failed to parse response: {}'.format(err))
            return None
        return data

    def make_subrequest(self, entity, url):
        data = self.parse_response(self.make_request('get', url))
        if len(data) < 1:
            self.log.warning(
                'No values in list for {} in {}'.format(entity, data))
            return None

        entity_data = data[0].get(entity, None)
        self.log.debug('{} is {}'.format(entity, entity_data))
        if not entity_data:
            self.log.warning('No entity {} in: {}'.format(entity, data))
            return None

        response = self.make_request('post', url + '/{}'.format(entity_data))
        self.log.debug('Entity subrequest response: {}'.format(response.text))
        return response

    def auth(self):
        self.log.info('Make authentication requests')
        self.make_request('get', self.xsrf_url)

        creds = {"username": self.username, "password": self.password}
        auth_response = self.make_request('post', self.auth_url, json=creds)
        self.log.debug('Auth response: {}'.format(auth_response.text))

        self.make_subrequest('contract_id', self.contract_url)
        self.make_subrequest('subscriber_id', self.subscriber_url)
        self.make_subrequest('product_id', self.product_url)
        self.log.info('Authentication complete')

    def run(self):
        self.log.info('Extract data from: {}'.format(self.data_url))
        data = self.parse_response(self.make_request('get', self.data_url))
        self.log.info('Extract data from: {}'.format(self.bonus_url))
        bonus = self.parse_response(self.make_request('get', self.bonus_url))
        if data:
            data['plan_speed'] = int(data.get('plan_speed', 0))
            data['status'] = int(data.get('status', None) == 'Активен')
            bs = data.get('blockStatus', {})
            data['is_voluntary_blocked'] = int(bs.get('isVoluntaryBlocked', 0))
            data['is_blocked'] = bs.get('isBlocked', 1)
        if bonus:
            last_pay_seconds = bonus.get('last_pay_dt', None)
            bonus['last_pay_dt'] = dp.parse(last_pay_seconds).timestamp()
        self.log.info('All data retrieved and corrected')
        return {'data': data, 'bonus': bonus}


class PaymentExporter:

    def __init__(self, args):
        try:
            with open(args.conf) as json_conf:
                config = json.load(json_conf)
        except Exception as err:
            logging.error('Failed to read config:', err)
            exit(0)

        log_format = config.get('log_format', ' '.join([
            "[%(levelname)s]",
            "%(module)s:%(funcName)s:%(lineno)d",
            "%(message)s",
        ]))
        logging.basicConfig(format=log_format)
        self.log = logging.getLogger()
        self.log.setLevel(
            getattr(logging, config.get('log_level', 'info').upper()))

        username = config.get('user', None)
        password = config.get('pass', None)

        if not username or not password:
            self.log.error('Not found user or pass in config')
            exit(0)

        self.port = config.get('port', 9999)
        if args.port is not None:
            self.port = args.port

        self.prefix = config.get('metrics_prefix', 'maryno_net')
        if args.prefix is not None:
            self.prefix = args.prefix

        self.sleep = config.get('sleep_time', 86400)
        if args.sleep is not None:
            self.sleep = args.sleep

        self.metrics = config.get('metrics', {
            "balance": {
                "desc": "Maryno.net provider balance in rub"
            },
            "plan_cost": {
                "desc": "Maryno.net provider plan price in rub"
            },
            "status": {
                "desc": "Maryno.net provider account status"
            },
            "is_blocked": {
                "desc": "Maryno.net provider account state"
            },
            "is_voluntary_blocked": {
                "desc": "Maryno.net provider account blocked by user state"
            },
        })

        base_url = config.get('base_url', 'https://lk.maryno.net')
        if args.url is not None:
            base_url = args.url

        self.log.info('Loaded config')

        self.extractor = Extractor(self.log, username, password, base_url)
        self.exporter_metrics = {}

        self.sleeped = False
        self.running = True
        self.end_event = threading.Event()
        self.__setup_signal_handlers()

    def __signal_stop_handler(self, signum, frame):
        # no time-consuming actions here!
        # just also sys.stderr.write is a bad idea
        self.running = False  # stop endless loop
        self.end_event.set()  # wake from sleep

    def __setup_signal_handlers(self):
        signal.signal(signal.SIGTERM, self.__signal_stop_handler)
        signal.signal(signal.SIGINT, self.__signal_stop_handler)

    def append_metrics(self, metrics):
        for metric in self.metrics:
            name = self.prefix + '_' + metric
            desc = metrics[metric].get('desc', metric)
            self.exporter_metrics[metric] = Gauge(name, desc)
        return self.exporter_metrics

    def fill_metrics(self, metrics):
        self.extractor.auth()
        info = self.extractor.run()
        for title, metric in self.metrics.items():
            info_type = metric.get('type', 'data')
            info_key = metric.get('key', title)
            value = info[info_type].get(info_key, None)
            self.exporter_metrics[title].set(value)

    def run(self):
        self.append_metrics(self.metrics)
        try:
            self.fill_metrics(self.metrics)
            self.sleeped = False
            # Start up the server to expose the metrics.
            start_http_server(self.port)
            self.log.info('Payment exporter started')
            while self.running:
                if self.sleeped:
                    self.fill_metrics(self.metrics)
                # sleep until timeout or end_event set
                # look for self.__signal_stop_handler
                self.end_event.wait(timeout=self.sleep)
                self.sleeped = True
        except BaseException:
            exception = sys.exc_info()
            error_tpl = 'Unexpected error {0} {1} {2}'
            self.log.error(error_tpl.format(*exception), exc_info=True)
        finally:
            self.log.info('PaymentExporter is shutting down')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Payment exporter')
    parser.add_argument('-c', '--conf', type=str, default='config.json',
                        help='Path to config JSON file')
    parser.add_argument('-p', '--prefix', type=str, default=None,
                        help='Prefix for exporter metrics')
    parser.add_argument('-u', '--url', type=str, default=None,
                        help='Base url for Maryno.net personal site')
    parser.add_argument('-s', '--sleep', type=str, default=None,
                        help='Sleep time in seconds')
    parser.add_argument('-P', '--port', type=int, default=None,
                        help='Exporter port')

    exporter = PaymentExporter(parser.parse_args())
    exporter.run()
