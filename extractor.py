import requests
import dateutil.parser as dp


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
            self.log.debug('Wrong response: {}'.format(response))
            return None
        try:
            data = response.json()
        except Exception as err:
            self.log.error('Failed to parse response: {}'.format(err))
            return None
        return data

    def make_subrequest(self, entity, url):
        data = self.parse_response(self.make_request('get', url))
        if not data:
            self.log.warning('Empty list for {} in {}'.format(entity, data))
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
        if not self.make_request('get', self.xsrf_url):
            self.log.warning('Failed to get first xsrf token')
            return False

        creds = {"username": self.username, "password": self.password}
        auth_response = self.make_request('post', self.auth_url, json=creds)
        self.log.debug('Auth response: {}'.format(auth_response.text))
        if not auth_response:
            self.log.warning('Failed to auth {}'.format(auth_response))
            return False

        chain = (
            ('contract_id', self.contract_url),
            ('subscriber_id', self.subscriber_url),
            ('product_id', self.product_url),
        )
        for entity, url in chain:
            if not self.make_subrequest(entity, url):
                self.log.warning('Failed to get {} {}'.format(entity, url))
                return False
        self.log.info('Authentication complete')
        return True

    def run(self):
        self.log.info('Extract data from: {}'.format(self.data_url))
        data = self.parse_response(self.make_request('get', self.data_url))
        self.log.info('Extract data from: {}'.format(self.bonus_url))
        bonus = self.parse_response(self.make_request('get', self.bonus_url))
        if data is not None:
            data['plan_speed'] = int(data.get('plan_speed', 0))
            data['status'] = int(data.get('status', None) == 'Активен')
            bs = data.get('blockStatus', {})
            data['is_voluntary_blocked'] = int(bs.get('isVoluntaryBlocked', 0))
            data['is_blocked'] = bs.get('isBlocked', 1)
        else:
            data = {}
        if bonus is not None:
            last_pay_seconds = bonus.get('last_pay_dt', 0)
            bonus['last_pay_dt'] = dp.parse(last_pay_seconds).timestamp()
        else:
            bonus = {}
        self.log.info('All data retrieved and corrected')
        return {'data': data, 'bonus': bonus}
