
from invoke import task, Exit, Collection
from os.path import join, dirname, exists
from jinja2 import Template
from configparser import ConfigParser
from io import StringIO
from difflib import unified_diff

CONFIG_PARAMETER_STORE_KEY = '/z2cw/config-ini'

cp = ConfigParser()


@task
def load_config(ctx):
    global cp
    cp.read('config.ini')


def config(option, section='DEFAULT'):
    sec = cp[section]
    if option in sec and sec[option]:
        return sec[option]


def profile_arg():
    if config('aws_profile'):
        return "--profile {}".format(config('aws_profile'))
    return ""


def verify_config(stack_name):
    if stack_name not in cp:
        print("{} stack is not configured".format(stack_name))
        raise Exit(1)


def stack_exists(ctx, stack_name):
    cmd = "aws {} cloudformation describe-stacks --stack-name {}" \
        .format(profile_arg(), stack_name)
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    return res.exited == 0


def s3_zipfile_exists(ctx, stack_name):
    cmd = "aws {} s3 ls s3://{}/z2cw/{}-function.zip" \
        .format(
            profile_arg(),
            config('lambda_code_bucket'),
            stack_name
        )
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    return res.exited == 0


@task(pre=[load_config])
def list_stacks(ctx):
    """list the stack sections in `config.ini`"""
    for stack in cp.sections():
        print("{} - {}".format(stack, config('vpsa_host', stack)))


@task(pre=[load_config])
def config_save(ctx):
    """sync your local `config.ini` contents to AWS Parameter Store"""
    s = StringIO()
    cp.write(s)
    raw_config = s.getvalue()
    cmd = ("aws {} ssm put-parameter --name {} --overwrite "
           "--type SecureString --value '{}' --output text"
           ).format(
        profile_arg(),
        CONFIG_PARAMETER_STORE_KEY,
        raw_config
    )
    new_version = ctx.run(cmd, hide=True).stdout.strip()
    print("new config version {} stored".format(new_version))


@task
def config_pull(ctx, version=None):
    """Overwrite your local `config.ini` with what's saved in AWS Parameter Store"""
    cmd = ("aws {} ssm get-parameter --name {} --output text "
           "--with-decryption --query 'Parameter.Value'") \
        .format(profile_arg(), CONFIG_PARAMETER_STORE_KEY)
    raw_config = ctx.run(cmd, hide=True).stdout.strip()
    with open('config.ini', 'w') as f:
        f.write(raw_config)
    print("local config is now up to date")


@task(pre=[load_config])
def config_check(ctx, diff=False):
    """Check that your local `config.ini` is in sync with AWS Parameter Store"""
    s = StringIO()
    cp.write(s)
    local_config = s.getvalue().strip()

    cmd = ("aws {} ssm get-parameter --name {} --with-decryption "
           "--query 'Parameter.Value' --output text").format(
        profile_arg(),
        CONFIG_PARAMETER_STORE_KEY
    )
    res = ctx.run(cmd, hide=True, warn=True)
    if res.failed and 'ParameterNotFound' in res.stderr:
        print("no remote config stored")
        print("run `invoke config.save` to store your local config remotely")
        return

    remote_config = res.stdout.strip()

    if local_config == remote_config:
        print("config is in sync")
    else:
        print("config is not in sync")
        print("run `invoke config.pull` to overwrite your local config with the remote version")
        print("run `invoke config.save` to overwrite remote config with your local changes")

        if diff:
            local_split = local_config.splitlines(True)
            remote_split = remote_config.splitlines(True)
            diff = unified_diff(local_split, remote_split, fromfile='local', tofile='remote')
            print(''.join(diff))
        else:
            print("run `invoke config.check --diff` to see the differences")




@task(pre=[load_config])
def package(ctx, stack_name):
    """
    Package the function + dependencies into a zipfile and upload to the configured `lambda_code_bucket`
    """
    verify_config(stack_name)
    build_path = join(dirname(__file__), 'dist')
    function_path = join(dirname(__file__), 'function.py')
    zip_path = join(dirname(__file__), 'function.zip')
    req_file = join(dirname(__file__), 'requirements.txt')
    ctx.run("pip install -U -r {} -t {}".format(req_file, build_path))
    ctx.run("ln -s -f -r -t {} {}".format(build_path, function_path))
    with ctx.cd(build_path):
        ctx.run("zip -r {} .".format(zip_path))
    s3_file_name = "{}-function.zip".format(stack_name)
    ctx.run("aws {} s3 cp {} s3://{}/z2cw/{}".format(
        profile_arg(),
        zip_path,
        config("lambda_code_bucket"),
        s3_file_name)
    )


@task(pre=[load_config])
def update_function(ctx, stack_name):
    """
    Update the function code with the latest packaged zipfile in s3. Note: this will publish a new Lambda version.
    """
    verify_config(stack_name)
    function_name = stack_name + '-function'
    cmd = ("aws {} lambda update-function-code "
           "--function-name {} --publish --s3-bucket {} --s3-key z2cw/{}-function.zip"
           ).format(
        profile_arg(),
        function_name,
        config('lambda_code_bucket'),
        stack_name
    )
    ctx.run(cmd)


@task(pre=[load_config])
def deploy(ctx, stack_name):
    """
    Create or update the CloudFormation stack. Note: you must run `package` first.
    """
    verify_config(stack_name)
    template_path = join(dirname(__file__), 'template.yml')

    if not s3_zipfile_exists(ctx, stack_name):
        print("No zipfile found in s3!")
        print("Did you run the `package` command?")
        raise Exit(1)

    create_or_update = stack_exists(ctx, stack_name) and 'update' or 'create'

    cmd = ("aws {} cloudformation {}-stack "
           "--stack-name {} "
           "--capabilities CAPABILITY_NAMED_IAM "
           "--template-body file://{} "
           "--tags Key=Project,Value=MH Key=OU,Value=DE "
           "--parameters "
           "ParameterKey=ZadaraApiKey,ParameterValue='{}' "
           "ParameterKey=ZadaraVpsaHost,ParameterValue='{}' "
           "ParameterKey=MetricNamespace,ParameterValue='{}' "
           "ParameterKey=MetricInterval,ParameterValue='{}' "
           "ParameterKey=VpcSubnetId,ParameterValue='{}' "
           "ParameterKey=VpcSecurityGroupId,ParameterValue='{}' "
           "ParameterKey=LambdaCodeBucket,ParameterValue='{}' "
           ).format(
        profile_arg(),
        create_or_update,
        stack_name,
        template_path,
        config('api_key', stack_name),
        config('vpsa_host', stack_name),
        config('metric_namespace', stack_name),
        config('metric_interval', stack_name),
        config('vpc_subnet_id', stack_name),
        config('vpc_security_group_id', stack_name),
        config('lambda_code_bucket')
    )
    print(cmd)
    ctx.run(cmd)
    __wait_for("create", stack_name)


@task(pre=[load_config])
def create_dashboard(ctx, stack_name, controller, volume, pool):
    """
    Create a CloudWatch dashboard as defined by cw_dashboard.json. You must provide the name of a controller, volume & pool present in the cloudwatch metric dimensions.
    """
    tf_path = join(dirname(__file__), 'cw_dashboard.json')
    with open(tf_path, 'r') as tf:
        t = Template(tf.read())
        dashboard_body = t.render(
            namespace=config('metric_namespace', stack_name),
            vpsa_host=config('vpsa_host', stack_name),
            controller=controller,
            volume=volume,
            pool=pool
        )
    cmd = ("aws {} cloudwatch put-dashboard "
           "--dashboard-name {}-metrics "
           "--dashboard-body '{}'") \
        .format(
            profile_arg(),
            stack_name,
            dashboard_body
        )
    ctx.run(cmd, echo=False)


@task(pre=[load_config])
def delete(ctx, stack_name):
    """
    Delete the CloudFormation stack
    """
    cmd = ("aws {} cloudformation delete-stack "
           "--stack-name {}").format(profile_arg(), stack_name)
    if input('are you sure? [y/N] ').lower().strip().startswith('y'):
        ctx.run(cmd)
        __wait_for("delete", stack_name)
    else:
        print("not deleting stack")


def __wait_for(ctx, op, stack_name):
    wait_cmd = ("aws {} cloudformation wait stack-{}-complete "
                "--stack-name {}").format(profile_arg(), op, stack_name)
    print("Waiting for stack {} to complete...".format(op))
    ctx.run(wait_cmd)
    print("Done")

ns = Collection()

stack_ns = Collection('stack')
stack_ns.add_task(package)
stack_ns.add_task(update_function)
stack_ns.add_task(deploy)
stack_ns.add_task(delete)
stack_ns.add_task(create_dashboard)
ns.add_collection(stack_ns)

config_ns = Collection('config')
config_ns.add_task(list_stacks)
config_ns.add_task(config_check, name='check')
config_ns.add_task(config_save, name='save')
config_ns.add_task(config_pull, name='pull')
ns.add_collection(config_ns)
