from hashlib import sha256


def hide_config(config):
    result = {}
    for key, value in config.items():
        if isinstance(value, dict):
            value = hide_config(value)
        elif 'password' in key:
            if value:
                value = sha256(config['password'].encode()).hexdigest()
            else:
                value = 'Empty field'
        result[key] = value
    return result
