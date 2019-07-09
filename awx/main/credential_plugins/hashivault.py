import copy
import os
import pathlib
import stat
import tempfile
import threading
from urllib.parse import urljoin

from .plugin import CredentialPlugin

import requests
from django.utils.translation import ugettext_lazy as _


base_inputs = {
    'fields': [{
        'id': 'url',
        'label': _('Server URL'),
        'type': 'string',
        'format': 'url',
        'help_text': _('The URL to the HashiCorp Vault'),
    }, {
        'id': 'token',
        'label': _('Token'),
        'type': 'string',
        'secret': True,
        'help_text': _('The access token used to authenticate to the Vault server'),
    }, {
        'id': 'cacert',
        'label': _('CA Certificate'),
        'type': 'string',
        'multiline': True,
        'help_text': _('The CA certificate used to verify the SSL certificate of the Vault server')
    }],
    'metadata': [{
        'id': 'secret_path',
        'label': _('Path to Secret'),
        'type': 'string',
        'help_text': _('The path to the secret e.g., /some-engine/some-secret/'),
    }],
    'required': ['url', 'token', 'secret_path'],
}

hashi_kv_inputs = copy.deepcopy(base_inputs)
hashi_kv_inputs['fields'].append({
    'id': 'api_version',
    'label': _('API Version'),
    'choices': ['v1', 'v2'],
    'help_text': _('API v1 is for static key/value lookups.  API v2 is for versioned key/value lookups.'),
    'default': 'v1',
})
hashi_kv_inputs['metadata'].extend([{
    'id': 'secret_key',
    'label': _('Key Name'),
    'type': 'string',
    'help_text': _('The name of the key to look up in the secret.'),
}, {
    'id': 'secret_version',
    'label': _('Secret Version (v2 only)'),
    'type': 'string',
    'help_text': _('Used to specify a specific secret version (if left empty, the latest version will be used).'),
}])
hashi_kv_inputs['required'].extend(['api_version', 'secret_key'])

hashi_ssh_inputs = copy.deepcopy(base_inputs)
hashi_ssh_inputs['metadata'] = [{
    'id': 'public_key',
    'label': _('Unsigned Public Key'),
    'type': 'string',
    'multiline': True,
}] + hashi_ssh_inputs['metadata'] + [{
    'id': 'role',
    'label': _('Role Name'),
    'type': 'string',
    'help_text': _('The name of the role used to sign.')
}, {
    'id': 'valid_principals',
    'label': _('Valid Principals'),
    'type': 'string',
    'help_text': _('Valid principals (either usernames or hostnames) that the certificate should be signed for.'),
}]
hashi_ssh_inputs['required'].extend(['public_key', 'role'])


def create_temporary_fifo(data):
    """Open fifo named pipe in a new thread using a temporary file path. The
    thread blocks until data is read from the pipe.
    Returns the path to the fifo.
    :param data(bytes): Data to write to the pipe.
    """
    path = os.path.join(tempfile.mkdtemp(), next(tempfile._get_candidate_names()))
    os.mkfifo(path, stat.S_IRUSR | stat.S_IWUSR)

    threading.Thread(
        target=lambda p, d: open(p, 'wb').write(d),
        args=(path, data)
    ).start()
    return path


def kv_backend(**kwargs):
    token = kwargs['token']
    url = urljoin(kwargs['url'], 'v1')
    secret_path = kwargs['secret_path']
    secret_key = kwargs.get('secret_key', None)
    cacert = kwargs.get('cacert', None)
    api_version = kwargs['api_version']

    request_kwargs = { 'timeout': 30 }
    if cacert:
        request_kwargs['verify'] = create_temporary_fifo(cacert.encode())

    sess = requests.Session()
    sess.headers['Authorization'] = 'Bearer {}'.format(token)

    if api_version == 'v2':
        if kwargs.get('secret_version'):
            request_kwargs['params'] = { 'version': kwargs['secret_version'] }
        try:
            mount_point, *path = pathlib.Path(secret_path.lstrip(os.sep)).parts
            '/'.join(path) 
        except Exception:
            mount_point, path = secret_path, []
        # https://www.vaultproject.io/api/secret/kv/kv-v2.html#read-secret-version
        request_url = '/'.join([url, mount_point, 'data'] + path).rstrip('/')
        response = sess.get(request_url, **request_kwargs)

        response.raise_for_status()
        json = response.json()['data']
    else:
        request_url = '/'.join([url, secret_path]).rstrip('/')
        response = sess.get(request_url, **request_kwargs)

        response.raise_for_status()
        json = response.json()

    if secret_key:
        try:
            return json['data'][secret_key]
        except KeyError:
            raise RuntimeError(
                '{} is not present at {}'.format(secret_key, secret_path)
            )
    return json['data']


def ssh_backend(**kwargs):
    token = kwargs['token']
    url = urljoin(kwargs['url'], 'v1')
    secret_path = kwargs['secret_path']
    role = kwargs['role']
    cacert = kwargs.get('cacert', None)

    request_kwargs = { 'timeout': 30 }
    if cacert:
        request_kwargs['verify'] = create_temporary_fifo(cacert.encode())

    request_kwargs['json'] = { 'public_key': kwargs['public_key'] }
    if kwargs.get('valid_principals'):
        request_kwargs['json']['valid_principals'] = kwargs['valid_principals']

    sess = requests.Session()
    sess.headers['Authorization'] = 'Bearer {}'.format(token)
    # https://www.vaultproject.io/api/secret/ssh/index.html#sign-ssh-key
    request_url = '/'.join([url, secret_path, 'sign', role]).rstrip('/')
    resp = sess.post(request_url, **request_kwargs)

    resp.raise_for_status()
    return resp.json()['data']['signed_key']


hashivault_kv_plugin = CredentialPlugin(
    'HashiCorp Vault Secret Lookup',
    inputs=hashi_kv_inputs,
    backend=kv_backend
)

hashivault_ssh_plugin = CredentialPlugin(
    'HashiCorp Vault Signed SSH',
    inputs=hashi_ssh_inputs,
    backend=ssh_backend
)
