#!/usr/bin/python3
import logging
import argparse
import json
import sys
import signal
import threading
from time import sleep
from prometheus_client import start_http_server, Gauge
from extractor import Extractor


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

        self.retry_limit = config.get('retry_limit', 2)
        if args.retry is not None:
            self.retry_limit = args.retry

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
        is_auth = False
        attempt = 0
        while not is_auth and attempt < self.retry_limit:
            is_auth = self.extractor.auth()
            attempt += 1
        if not is_auth:
            self.log.error('Failed to auth {} times'.format(self.retry_limit))
            return False

        info = self.extractor.run()
        for title, metric in self.metrics.items():
            info_type = metric.get('type', 'data')
            info_key = metric.get('key', title)
            value = info[info_type].get(info_key, None)
            self.exporter_metrics[title].set(value)
        return True

    def run(self):
        self.append_metrics(self.metrics)
        try:
            if not self.fill_metrics(self.metrics):
                raise Exception('Failed to init metrics')
            self.sleeped = False
            # Start up the server to expose the metrics.
            start_http_server(self.port)
            self.log.info('Payment exporter started')
            # Wait first interval outside the loop
            self.end_event.wait(timeout=self.sleep)
            while self.running:
                self.fill_metrics(self.metrics)
                # sleep until timeout or end_event set
                # look for self.__signal_stop_handler
                self.end_event.wait(timeout=self.sleep)
        except BaseException:
            exception = sys.exc_info()
            error_tpl = 'Unexpected error {0} {1} {2}'
            self.log.error(error_tpl.format(*exception), exc_info=True)
        finally:
            self.log.info('PaymentExporter is shutting down')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Payment exporter')
    parser.add_argument('-c', '--conf', type=str, default='payment_exporter.config.json',
                        help='Path to config JSON file')
    parser.add_argument('-p', '--prefix', type=str, default=None,
                        help='Prefix for exporter metrics')
    parser.add_argument('-u', '--url', type=str, default=None,
                        help='Base url for Maryno.net personal site')
    parser.add_argument('-s', '--sleep', type=str, default=None,
                        help='Sleep time in seconds')
    parser.add_argument('-P', '--port', type=int, default=None,
                        help='Exporter port')
    parser.add_argument('-r', '--retry', type=int, default=None,
                        help='Auth retry attempts limit')

    exporter = PaymentExporter(parser.parse_args())
    exporter.run()
