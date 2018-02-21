
from invoke import task, Exit
from os import getenv as env
from dotenv import load_dotenv
from os.path import join, dirname, exists

load_dotenv(join(dirname(__file__), '.env'))


def getenv(var, required=True):
    val = env(var)
    if required and val is None:
        raise Exit("{} not defined".format(var))
    return val


def profile_arg():
    profile = getenv("AWS_PROFILE", False)
    if profile is not None:
        return "--profile {}".format(profile)
    return ""


def stack_exists(ctx):
    cmd = "aws {} cloudformation describe-stacks --stack-name {}" \
        .format(profile_arg(), getenv('STACK_NAME'))
    res = ctx.run(cmd, hide=True, warn=True, echo=False)
    return res.exited == 0


@task
def create_code_bucket(ctx):
    """
    Create the s3 bucket for storing packaged lambda code
    """
    code_bucket = getenv('LAMBDA_CODE_BUCKET')
    cmd = "aws {} s3 ls {}".format(profile_arg(), code_bucket)
    exists = ctx.run(cmd, hide=True, warn=True)
    if exists.ok:
        print("Bucket exists!")
    else:
        cmd = "aws {} s3 mb s3://{}".format(profile_arg(), code_bucket)
        ctx.run(cmd)


@task
def package(ctx):

    build_path = join(dirname(__file__), 'dist')
    function_path = join(dirname(__file__), 'function.py')
    zip_path = join(dirname(__file__), 'function.zip')
    req_file = join(dirname(__file__), 'requirements.txt')
    ctx.run("pip install -U -r {} -t {}".format(req_file, build_path))
    ctx.run("ln -s -f -r -t {} {}".format(build_path, function_path))
    with ctx.cd(build_path):
        ctx.run("zip -r {} .".format(zip_path))
    s3_file_name = "{}-function.zip".format(getenv('STACK_NAME'))
    ctx.run("aws {} s3 cp {} s3://{}/{}".format(
        profile_arg(),
        zip_path,
        getenv("LAMBDA_CODE_BUCKET"),
        s3_file_name)
    )


@task
def update(ctx):
    function_name = "{}-function".format(getenv("STACK_NAME"))
    cmd = ("aws {} lambda update-function-code "
           "--function-name {} --publish --s3-bucket {} --s3-key {}-function.zip"
           ).format(
        profile_arg(),
        function_name,
        getenv('LAMBDA_CODE_BUCKET'),
        getenv('STACK_NAME')
    )
    ctx.run(cmd)

@task
def deploy(ctx):

    template_path = join(dirname(__file__), 'template.yml')
    zip_path = join(dirname(__file__), 'function.zip')

    if not exists(zip_path):
        print("No zip found!")
        print("Did you run the package-* commands?")
        raise Exit(1)

    create_or_update = stack_exists(ctx) and 'update' or 'create'

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
        getenv("STACK_NAME"),
        template_path,
        getenv('API_KEY'),
        getenv('VPSA_HOST'),
        getenv('METRIC_NAMESPACE'),
        getenv('METRIC_INTERVAL'),
        getenv('VPC_SUBNET_ID'),
        getenv('VPC_SECURITY_GROUP_ID'),
        getenv('LAMBDA_CODE_BUCKET')
    )
    ctx.run(cmd)

@task
def delete(ctx):
    cmd = ("aws {} cloudformation delete-stack "
           "--stack-name {}").format(profile_arg(), getenv('STACK_NAME'))
    ctx.run(cmd)

