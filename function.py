
import re
import json
import boto3
import arrow
from os import getenv as env
from operator import itemgetter
from urllib.parse import urljoin

from botocore.exceptions import ClientError
from botocore.vendored import requests

from botocore.vendored.requests.packages import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

API_KEY = env('API_KEY')
VPSA_HOST = env('VPSA_HOST')
METRIC_INTERVAL = env('METRIC_INTERVAL', 30)
METRIC_NAMESPACE = env('METRIC_NAMESPACE')
LAST_MESSAGE_ID_PARAM_NAME = env('LAST_MESSAGE_ID_PARAM_NAME')
VPSA_LOG_GROUP_NAME = env('VPSA_LOG_GROUP_NAME')
AWS_PROFILE = env('AWS_PROFILE')

if AWS_PROFILE is not None:
    boto3.setup_default_session(profile_name=AWS_PROFILE)

cw = boto3.client('cloudwatch')
ssm = boto3.client('ssm')
cwlogs = boto3.client('logs')
s = requests.Session()
s.headers.update({'X-Access-Key': API_KEY})


def api_request(path, params=None):
    url = urljoin('https://' + VPSA_HOST + '/api/', path)
    r = s.get(url, params=params, verify=False)
    r.raise_for_status()
    return r.json()


def get_resources(resouce_type):
    data = api_request('{}.json'.format(resouce_type))
    return data['response'][resouce_type]


def get_metrics(resource_type, resource_name, custom_path=None):
    if custom_path is not None:
        path = custom_path
    else:
        path = '{}/{}/performance.json'.format(resource_type, resource_name)
    data = api_request(path, {'interval': METRIC_INTERVAL})
    return data['response']['usages']


def send2cw(metric_batch, extra_dimensions=None):

    dimensions = [{'Name': 'vpsa', 'Value': VPSA_HOST}]
    if extra_dimensions is not None:
        dimensions.extend([
            {'Name': k, 'Value': v} for k, v in extra_dimensions.items()
        ])

    for measurements  in metric_batch:
        metric_data = []
        timestamp = arrow.get(measurements['time']).datetime
        del measurements['time']
        for metric_name, val in measurements.items():
            metric_data.append({
                'MetricName': metric_name,
                'Timestamp': timestamp,
                'Value': val,
                'Unit': get_unit(metric_name),
                'Dimensions': dimensions
            })
            if len(metric_data) == 10:
                cw.put_metric_data(
                    Namespace=METRIC_NAMESPACE,
                    MetricData=metric_data
                )
                metric_data = []
                continue

        if len(metric_data):
            cw.put_metric_data(
                Namespace=METRIC_NAMESPACE,
                MetricData=metric_data
            )

def get_unit(metric_name):
    if metric_name.endswith('time'):
        return 'Milliseconds'
    elif metric_name.endswith('bandwidth'):
        return 'Megabytes'
    elif re.match('(cpu|mem|zcache)_', metric_name):
        return 'Percent'
    else:
        return 'Count'


def get_last_message_id():
    try:
        param = ssm.get_parameter(Name=LAST_MESSAGE_ID_PARAM_NAME)
        print("got last message id {}".format(param['Parameter']['Value']))
        return param['Parameter']['Value']
    except ClientError as e:
        if e.response['Error']['Code'] == 'ParameterNotFound':
            return "0"
        else:
            raise


def set_last_message_id(id):
    print("setting last message id {}".format(id))
    ssm.put_parameter(
        Name=LAST_MESSAGE_ID_PARAM_NAME,
        Type='String',
        Value=id,
        Overwrite=True
    )


def send2cwlogs(msgs, sequence_token):

    log_stream_name = VPSA_HOST.replace(':', '-')

    put_params = {
        'logGroupName': VPSA_LOG_GROUP_NAME,
        'logStreamName': log_stream_name,
        'logEvents': []
    }

    try:
        if sequence_token is None:
            sequence_token = cwlogs.describe_log_streams(
                logGroupName=VPSA_LOG_GROUP_NAME,
                logStreamNamePrefix=log_stream_name
            )['logStreams'][0]['uploadSequenceToken']
        put_params['sequenceToken'] = sequence_token
    except KeyError as e:
        pass

    for msg in msgs:
        timestamp = arrow.get(msg['msg_time']).timestamp * 1000
        del msg['msg_time']
        put_params['logEvents'].append({
            'timestamp': timestamp,
            'message': json.dumps(msg)
        })
        put_params['logEvents'] = sorted(put_params['logEvents'], key=itemgetter('timestamp'))

    resp = cwlogs.put_log_events(**put_params)
    return resp['nextSequenceToken']


def send_logs():

    start_msg_id = get_last_message_id()
    sort = json.dumps([{"property":"msg-id","direction":"ASC"}])
    params = {'limit': 100, 'start': start_msg_id, 'sort': sort}
    batch_counter = 0
    sequence_token = None

    msgs = []
    batch_first_timestamp = None

    while True:
        resp = api_request('messages.json', params=params)
        if not resp['response']['messages']:
            break

        for msg in resp['response']['messages']:

            msg_timestamp = arrow.get(msg['msg_time']).timestamp
            if batch_first_timestamp is None:
                batch_first_timestamp = msg_timestamp

            # batch can't span more than 24hr
            if msg_timestamp - batch_first_timestamp >= 86400:
                batch_counter += 1
                print("sending batch {}".format(batch_counter))
                sequence_token = send2cwlogs(msgs, sequence_token)
                last_id = msgs[-1]['msg_id']
                set_last_message_id(last_id)
                batch_first_timestamp = None
                msgs = []
            else:
                msgs.append(msg)

        if len(msgs):
            batch_counter += 1
            print("sending batch {}".format(batch_counter))
            sequence_token = send2cwlogs(msgs, sequence_token)
            last_id = msgs[-1]['msg_id']
            set_last_message_id(last_id)
            msgs = []

        params['start'] = last_id


def send_pool_capcity(pool):
    capacity = float(pool['capacity'])
    available_capacity = float(pool['available_capacity'])
    percent_available = round( (available_capacity / capacity) * 100, 2)

    dimensions = [
        {
            'Name': 'vpsa',
            'Value': VPSA_HOST
        },
        {
            'Name': 'pool',
            'Value': pool['name']
        }
    ]

    metric_data = [
        {
            'MetricName': 'available_capacity',
            'Timestamp': arrow.utcnow().datetime,
            'Value': available_capacity,
            'Unit': 'Gigabytes',
            'Dimensions': dimensions
        },
        {
            'MetricName': 'percent_available',
            'Timestamp': arrow.utcnow().datetime,
            'Value': percent_available,
            'Unit': 'Percent',
            'Dimensions': dimensions
        }
    ]

    cw.put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=metric_data
    )


def send_volume_capacity(volume):

    dimensions = [
        {
            'Name': 'vpsa',
            'Value': VPSA_HOST
        },
        {
            'Name': 'volume',
            'Value': volume['name']
        }
    ]

    metric_data = []
    for metric in ['allocated_capacity', 'data_copies_capacity']:
        value = float(volume[metric])
        metric_data.append({
            'MetricName': metric,
            'Timestamp': arrow.utcnow().datetime,
            'Value': value,
            'Unit': 'Gigabytes',
            'Dimensions': dimensions
        })

    cw.put_metric_data(
        Namespace=METRIC_NAMESPACE,
        MetricData=metric_data
    )


def handler(event, context):
    """Fetch performance data from zadara api and push to cloudwatch"""

    print(event)

    print("sending logs")
    send_logs()

    for pool in get_resources('pools'):
        print("publishing metrics for pool {}".format(pool['name']))
        pool_perf = get_metrics('pools', pool['name'])
        send2cw(pool_perf, {'pool': pool['name']})
        send_pool_capcity(pool)

    active_servers = []
    for volume in get_resources('volumes'):
        print("publishing metrics for volume {}".format(volume['name']))
        vol_perf = get_metrics('volumes', volume['name'])
        send2cw(vol_perf, {'volume': volume['name']})
        active_servers.append(volume['server_name'])
        send_volume_capacity(volume)

    for server in get_resources('servers'):
        if server['display_name'] not in active_servers:
            print("skipping metrics for server {}".format(server['name']))
            continue
        print("publishing metrics for server {}".format(server['name']))
        server_perf = get_metrics('servers', server['name'])
        send2cw(server_perf, {'server': server['name']})

    for vc in get_resources('vcontrollers'):
        if vc['state'] != 'active':
            print("skipping metrics for controller {}".format(vc['name']))
            continue
        print("publishing metrics for controller {}".format(vc['name']))
        vc_perf = get_metrics('vcontrollers', vc['name'])
        send2cw(vc_perf, {'controller': vc['name']})

    print("publishing vcache performance metrics")
    vcache_perf = get_metrics(None, None, custom_path='vcontrollers/cache_performance.json')
    send2cw(vcache_perf)

    print("publishing vcache stats metrics")
    vcache_stats = get_metrics(None, None, custom_path='vcontrollers/cache_stats.json')
    send2cw(vcache_stats)

    print("done!")

if __name__ == '__main__':
    handler({}, None)
