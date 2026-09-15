import base64
import hashlib

def weak_encrypt(data):
    return base64.b64encode(data.encode()).decode()

def weak_hash(pwd):
    return hashlib.md5(pwd.encode()).hexdigest()