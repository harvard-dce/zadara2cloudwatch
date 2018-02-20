import boto3
import arrow
from os import getenv as env
from urllib.parse import urljoin
from botocore.vendored import requests

API_KEY = env('API_KEY')
VPSA_HOST = env('VPSA_HOST')
METRIC_INTERVAL = env('METRIC_INTERVAL', 30)
METRIC_NAMESPACE = env('METRIC_NAMESPACE')

cw = boto3.client('cloudwatch')
s = requests.Session()
s.headers.update({'X-Access-Key': API_KEY})


def api_request(path, params=None):
    url = urljoin('https://' + VPSA_HOST + '/api/', path)
    r = s.get(url, params=params, verify=False)
    r.raise_for_status()
    return r.json()


def get_pools():
    data = api_request('pools.json')
    return data['response']['pools']


def get_pool_performance(pool_name):
    path = 'pools/{}/performance.json'.format(pool_name)
    data = api_request(path, {'interval': METRIC_INTERVAL})
    return data['response']['usages']


def send2cw(metric_batch, dimensions={}):
    put_data = {
        'Namespace': METRIC_NAMESPACE,
        'MetricData': []
    }
    for measurements  in metric_batch:
        timestamp = arrow.get(measurements['time']).datetime
        del measurements['time']
        for metric_name, val in measurements.items():
            put_data['MetricData'].append({
                'MetricName': metric_name,
                'Timestamp': timestamp,
                'Value': val,
                'Unit': get_unit(metric_name),
                'Dimensions': [
                    {'vpsa': VPSA_HOST}.update(dimensions)
                ]
            })
    print(put_data)


def get_unit(metric_name):
    if metric_name.endswith('time'):
        return 'Milliseconds'
    elif metric_name.endswith('bandwidth'):
        return 'Megabytes'
    else:
        return 'Count'


def handler(event, context):
    """Fetch performance data from zadara api and push to cloudwatch"""
    for pool in get_pools():
        pool_perf = get_pool_performance(pool['name'])
        send2cw(pool_perf, {'pool': pool['name']})


if __name__ == '__main__':
    handler({}, None)