def task(data):
    import hashlib
    return [{'input': value, 'digest': hashlib.pbkdf2_hmac('sha256', str(value).encode(), b'DistributedExec', 200000).hex()}
            for value in data]
