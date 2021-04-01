from hashlib import sha256


def hide_config(config):
    filtered = filter(lambda x: x[0] != 'pass', config.items())
    result = {key: value for key, value in filtered}
    if config['pass']:
        result['pass'] = sha256(config['pass'].encode()).hexdigest()
    else:
        result['pass'] = 'Empty field'
    return result
