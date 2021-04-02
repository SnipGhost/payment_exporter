#!/usr/bin/python3
import logging
import argparse
import json
import sys
import signal
import threading
from prometheus_client import start_http_server, Gauge
from utils import hide_config
import modules


class PaymentExporter:

    def __init__(self, args):
        self.conf = self._load_config(args)
        logging.basicConfig(format=self.conf['log_format'])
        self.log = logging.getLogger()
        self.log.setLevel(getattr(logging, self.conf['log_level']))
        self.log.info('Loaded config')
        self.log.debug('Config: {}'.format(hide_config(self.conf)))

        # Construct used extractors
        self.extractors = {}
        for prefix, extractor in self.conf['extractors'].items():
            class_name = extractor.get('module', 'Extractor')
            extractor_class = getattr(modules, class_name)
            self.extractors[prefix] = extractor_class(self.log, **extractor)
        # Create used metrics
        self.exporter_metrics = {}
        for prefix, definition in self.conf['metrics'].items():
            self.append_metrics(prefix, definition)

        self.running = True
        self.end_event = threading.Event()
        self.__setup_signal_handlers()

    def _load_config(self, args):
        # Default log format (not joined yet)
        log_format = [
            "[%(levelname)s]",
            "%(module)s:%(funcName)s:%(lineno)d",
            "%(message)s",
        ]
        # Default config
        config = {
            'log_level': 'INFO',     # logging level
            'log_format': None,      # logging format (None for default)
            'sleep': 43200,          # time to sleep in seconds before collect
            'port': 9999,            # port binding to expose metrics
            'extractors': {          # definition of prefixes and extractors
                'maryno_net': {
                    'module': 'MarynoNetExtractor',
                    'username': '',  # maryno.net account (required)
                    'password': '',  # maryno.net password (required)
                }
            },
            'metrics': {             # definition of used prefixes and metrics
                'maryno_net': {      # metrics prefix like node_exporter_*
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
                },
            },
        }
        # Load config file and overwrite defaults
        try:
            with open(args.config) as file:
                data = json.load(file)
                config.update(data)
        except Exception as err:
            logging.error('Failed to read config:', err)
        # Set default log_format
        if not config['log_format']:
            config['log_format'] = ' '.join(log_format)
        # Parse args and overwrite loaded config
        for key, value in filter(lambda x: x[1], vars(args).items()):
            config[key] = value
        # Convert to uppercase to find logging const attribute
        if config['log_level']:
            config['log_level'] = config['log_level'].upper()
        return config

    def __signal_stop_handler(self, signum, frame):
        # No time-consuming actions here!
        # Just also sys.stderr.write is a bad idea
        self.running = False  # Stop endless loop
        self.end_event.set()  # Wake from sleep

    def __setup_signal_handlers(self):
        signal.signal(signal.SIGTERM, self.__signal_stop_handler)
        signal.signal(signal.SIGINT, self.__signal_stop_handler)

    def append_metrics(self, prefix, metrics):
        for metric in metrics:
            name = prefix + '_' + metric
            desc = metrics[metric].get('desc', metric)
            self.exporter_metrics[metric] = Gauge(name, desc)
        return self.exporter_metrics

    def fill_metrics(self, all_metrics):
        for prefix, metrics in all_metrics.items():
            extractor = self.extractors[prefix]
            info = extractor.run()
            if not info:
                self.log.warning('Wrong data from {}'.format(extractor))
                return False
            for title, metric in metrics.items():
                info_type = metric.get('type', 'data')
                info_key = metric.get('key', title)
                info_dict = info.get(info_type, None)
                if info_dict:
                    value = info_dict.get(info_key, 0)
                    self.exporter_metrics[title].set(value)
        return True

    def run(self):
        try:
            if not self.fill_metrics(self.conf['metrics']):
                raise Exception('Failed to init metrics')
            self.sleeped = False
            # Start up the server to expose the metrics.
            start_http_server(self.conf['port'])
            self.log.info('Payment exporter started')
            # Wait first interval before run fill_metrics
            self.end_event.wait(timeout=self.conf['sleep'])
            while self.running:
                self.fill_metrics(self.conf['metrics'])
                # Sleep until timeout or end_event set
                # Look for self.__signal_stop_handler
                self.end_event.wait(timeout=self.conf['sleep'])
        except BaseException:
            exception = sys.exc_info()
            error_tpl = 'Unexpected error {0} {1} {2}'
            self.log.error(error_tpl.format(*exception), exc_info=True)
        finally:
            self.log.info('PaymentExporter is shutting down')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Payment exporter')
    parser.add_argument('-c', '--config', type=str, default='payment_exporter.config.json',
                        help='Path to config JSON file')
    parser.add_argument('-s', '--sleep', type=str, default=None,
                        help='Sleep time in seconds, a low value may cause rate-limit')
    parser.add_argument('-P', '--port', type=int, default=None,
                        help='Exporter port')
    parser.add_argument('-l', '--log-level', type=str, default=None,
                        help='Logging level from {debug, info, warning, error}')
    exporter = PaymentExporter(parser.parse_args())
    exporter.run()
