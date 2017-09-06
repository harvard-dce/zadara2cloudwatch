
import csv
import boto3
import arrow
import click
from itertools import filterfalse


@click.group()
def cli():
    pass


@cli.command()
@click.option('-n', '--namespace', default='ZadaraVPSA')
@click.option('-a', '--vpsa')
@click.option('-v', '--volume')
@click.option('-s', '--start')
@click.option('-e', '--end')
@click.option('-i', '--infile')
@click.option('-p', '--profile', default='test')
@click.option('-d', '--dryrun', is_flag=True)
def push_metrics(namespace, vpsa, volume, start, end, infile, profile, dryrun):
    boto3.setup_default_session(profile_name=profile)
    cw = boto3.client('cloudwatch')
    metrics = {
        'read.iops' : 'Count',
        'read.active_ios' : 'None',
        'read.io_errors' : 'Count',
        'read.mbps' : 'Megabytes/Second',
        'read.latency_ms': 'Milliseconds',
        'read.max_latency_ms': 'Milliseconds',
        'write.iops' : 'Count',
        'write.active_ios' : 'None',
        'write.io_errors' : 'Count',
        'write.mbps': 'Megabytes/Second',
        'write.latency_ms': 'Milliseconds',
        'write.max_latency_ms': 'Milliseconds'
    }

    if start is not None:
        start = arrow.get(start)
    if end is not None:
        end = arrow.get(end)

    with open(infile) as f:
        reader = csv.DictReader(f)
        reader = filterfalse(lambda x: x['dev_ext_name'] != volume, reader)
        count = 0
        for row in reader:

            timestamp = arrow.get(row['unixtime'])
            if start is not None and timestamp < start:
                continue
            if end is not None and timestamp > end:
                continue

            metric_data = []
            dimensions = [
                { 'Name': 'vpsa', 'Value': vpsa },
                { 'Name': 'volume', 'Value': row['dev_ext_name'] }
            ]

            for metric, unit in metrics.items():
                metric_value = row[metric] and float(row[metric]) or 0
                metric_data.append({
                    'MetricName': metric.replace('.', '_'),
                    'Dimensions': dimensions,
                    'Timestamp': timestamp.datetime.isoformat(),
                    'Unit': unit,
                    'Value': metric_value
                })

            if not dryrun:
                res = cw.put_metric_data(
                    Namespace=namespace,
                    MetricData= metric_data
                )

            count += 1
            if count % 1000 == 0:
                print("processed %d rows" % count)

        print("finished processing %d rows" % count)

if __name__ == '__main__':
    cli()
