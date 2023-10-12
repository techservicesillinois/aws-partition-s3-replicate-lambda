from collections import namedtuple
from datetime import datetime
import json
import os
from os.path import dirname, join as path_join
import sys
from unittest.mock import patch
from urllib.parse import urlencode

import boto3
from moto import mock_dynamodb, mock_iam, mock_kms, mock_s3, mock_secretsmanager, mock_sqs, mock_sts
import pytest

import yaml
try:
    from yaml import CLoader as YAMLLoader
except ImportError:
    from yaml import Loader as YAMLLoader

FIXTURES = path_join(dirname(__file__), 'fixtures')
BASE = dirname(dirname(__file__))
sys.path.insert(0, path_join(BASE, 'build'))

for name in ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SECURITY_TOKEN', 'AWS_SESSION_TOKEN']:
    os.environ[name] = 'testing'

os.environ['DEST_BUCKET'] = 'dest-bucket'
os.environ['DEST_KMS_KEY'] = ''
os.environ['DEST_SECRET'] = 'dest-secret'

os.environ['OBJECTS_QUEUE'] = ''
os.environ['OBJECTS_TABLE'] = 'objects'

AccountSetup = namedtuple(
    'AccountSetup',
    ['src_kms_key_id', 'dst_kms_key_id', 'dst_secret_arn'],
)

class LambdaContext:
    def __init__(self):
        self.function_name = 'test-replicate'

    def get_remaining_time_in_millis(self):
        return 60000

def _populate_bucket(bucket_name, kms_key_id=None):
    bucket = boto3.resource('s3').Bucket(bucket_name)
    client = boto3.client('s3')

    fixtures = path_join(FIXTURES, 'source-bucket')
    objects = {}
    for dirpath, _, filenames in os.walk(fixtures):
        for filename in sorted(filenames):
            if not filename.endswith(('.yml', '.yaml')):
                continue

            with open(path_join(dirpath, filename), 'rb') as file_fh:
                file_data = yaml.load(file_fh, Loader=YAMLLoader)

            key = file_data['key']
            extra_args = file_data.get('extra_args', {})
            tags = file_data.get('tags', {})

            if kms_key_id:
                extra_args['ServerSideEncryption'] = 'aws:kms'
                extra_args['SSEKMSKeyId'] = kms_key_id

            objects[key] = []
            for ver_data in file_data.get('versions', []):
                ver_extra_args = extra_args.copy()
                ver_extra_args.update(**ver_data.get('extra_args', {}))
                ver_content = ver_data.get('content', '').encode('utf-8')

                ver_tags = ver_data.get('tags', tags)
                if ver_tags:
                    ver_extra_args['Tagging'] = urlencode(ver_tags)

                bucket.put_object(
                    Key=key,
                    Body=ver_content,
                    **ver_extra_args
                )

                bucket_obj = client.head_object(
                    Bucket=bucket_name,
                    Key=key,
                )
                bucket_obj.pop('ResponseMetadata', None)
                bucket_obj['content'] = ver_content
                bucket_obj['tags'] = ver_tags
                objects[key].append(bucket_obj)
    return objects

@pytest.fixture
def dynamodb_client():
    with mock_dynamodb():
        yield boto3.client('dynamodb')

@pytest.fixture
def iam_client():
    with mock_iam():
        yield boto3.client('iam')

@pytest.fixture
def iam_dst_creds(iam_client):
    with patch.dict(os.environ, {'MOTO_ACCOUNT_ID': '999999999999'}):
        iam_client.create_user(UserName='replicate-test')
        return iam_client.create_access_key(UserName='replicate-test')['AccessKey']

@pytest.fixture
def kms_client():
    with mock_kms():
        yield boto3.client('kms')

@pytest.fixture
def lambda_context():
    return LambdaContext()

@pytest.fixture
def s3_client():
    with mock_s3():
        yield boto3.client('s3', region_name='us-east-2')

@pytest.fixture
def secretsmanager_client():
    with mock_secretsmanager():
        yield boto3.client('secretsmanager')

@pytest.fixture
def sqs_client():
    with mock_sqs():
        yield boto3.client('sqs')

@pytest.fixture
def sts_client():
    with mock_sts():
        yield boto3.client('sts')

@pytest.fixture
def setup_accounts(monkeypatch, dynamodb_client, iam_dst_creds, kms_client, secretsmanager_client, sqs_client):
    import partition_s3_replicate
    from partition_s3_replicate import (
        DST_SECRET,
        OBJECTS_TABLE
    )

    monkeypatch.setattr(partition_s3_replicate.ReplicateObject, '_dst_creds', {})

    with patch.dict(os.environ, {'MOTO_ACCOUNT_ID': '999999999999'}):
        dst_kms_key_id = kms_client.create_key(KeyUsage='ENCRYPT_DECRYPT')['KeyMetadata']['KeyId']
    monkeypatch.setattr(partition_s3_replicate, 'DST_KMS_KEY', dst_kms_key_id)

    src_kms_key_id = kms_client.create_key(KeyUsage='ENCRYPT_DECRYPT')['KeyMetadata']['KeyId']

    dst_secret_arn = secretsmanager_client.create_secret(
        Name=DST_SECRET,
        SecretString=json.dumps({
            'user': iam_dst_creds['UserName'],
            'accesskey': iam_dst_creds['AccessKeyId'],
            'secretkey': iam_dst_creds['SecretAccessKey'],
        }),
    )['ARN']

    obj_queue = sqs_client.create_queue(
        QueueName='objects.fifo',
        Attributes={
            'FifoQueue': 'true',
            'ContentBasedDeduplication': 'true',
            'MessageRetentionPeriod': str(15*60),
            'VisibilityTimeout': str(5*60),
        },
    )['QueueUrl']
    monkeypatch.setattr(partition_s3_replicate, 'OBJECTS_QUEUE', obj_queue)

    dynamodb_client.create_table(
        AttributeDefinitions=[
            {'AttributeName': 'Key', 'AttributeType': 'S'},
            {'AttributeName': 'VersionId', 'AttributeType': 'S'},
        ],
        TableName=OBJECTS_TABLE,
        KeySchema=[
            {'AttributeName': 'Key', 'KeyType': 'HASH'},
            {'AttributeName': 'VersionId', 'KeyType': 'RANGE'},
        ],
        BillingMode='PAY_PER_REQUEST',
        TableClass='STANDARD',
    )

    return AccountSetup(
        src_kms_key_id=src_kms_key_id,
        dst_kms_key_id=dst_kms_key_id,
        dst_secret_arn=dst_secret_arn,
    )

@pytest.fixture
def setup_s3(request, setup_accounts, s3_client):
    import partition_s3_replicate

    params = getattr(
        request,
        'param',
        {}
    )
    params.setdefault('encryption', True)
    params.setdefault('versioning', True)

    s3_client.create_bucket(
        ACL='private',
        Bucket='source-bucket',
        CreateBucketConfiguration={'LocationConstraint': 'us-east-2'},
        ObjectOwnership='BucketOwnerPreferred',
    )
    if params['encryption']:
        s3_client.put_bucket_encryption(
            Bucket='source-bucket',
            ServerSideEncryptionConfiguration={
                'Rules': [
                    {
                        'ApplyServerSideEncryptionByDefault': {
                            'SSEAlgorithm': 'aws:kms',
                            'KMSMasterKeyID': setup_accounts.src_kms_key_id,
                        },
                    },
                ],
            },
        )
    if params['versioning']:
        s3_client.put_bucket_versioning(
            Bucket='source-bucket',
            VersioningConfiguration={'Status': 'Enabled'},
        )
    src_bucket_objects = _populate_bucket('source-bucket', setup_accounts.src_kms_key_id)


    with patch.dict(os.environ, {'MOTO_ACCOUNT_ID': '999999999999'}):
        s3_client.create_bucket(
            ACL='private',
            Bucket=partition_s3_replicate.DST_BUCKET,
            CreateBucketConfiguration={'LocationConstraint': 'us-east-2'},
            ObjectOwnership='BucketOwnerPreferred',
        )
        if params['encryption']:
            s3_client.put_bucket_encryption(
                Bucket=partition_s3_replicate.DST_BUCKET,
                ServerSideEncryptionConfiguration={
                    'Rules': [
                        {
                            'ApplyServerSideEncryptionByDefault': {
                                'SSEAlgorithm': 'aws:kms',
                                'KMSMasterKeyID': setup_accounts.dst_kms_key_id,
                            },
                        },
                    ],
                },
            )
        if params['versioning']:
            s3_client.put_bucket_versioning(
                Bucket=partition_s3_replicate.DST_BUCKET,
                VersioningConfiguration={'Status': 'Enabled'},
            )

    return src_bucket_objects

@pytest.fixture
def setup_s3_destobjs(setup_accounts, setup_s3):
    import partition_s3_replicate

    objects_table = boto3.resource('dynamodb').Table(partition_s3_replicate.OBJECTS_TABLE)

    dst_bucket_objects = _populate_bucket(partition_s3_replicate.DST_BUCKET, setup_accounts.dst_kms_key_id)

    for key, src_object_vers in setup_s3.items():
        for src_object_ver_idx, src_object_ver in enumerate(src_object_vers):
            dst_object_ver = dst_bucket_objects[key][src_object_ver_idx]

            obj_item = {
                'Key': key,
                'VersionId': src_object_ver.get('VersionId') or '$null',
                'DestObject': dst_object_ver.copy(),
                'DestObjectTags': dst_object_ver['tags'],
            }
            del obj_item['DestObject']['content']
            del obj_item['DestObject']['tags']

            for name, value in obj_item['DestObject'].items():
                if isinstance(value, datetime):
                    obj_item['DestObject'][name] = value.isoformat()

            objects_table.put_item(Item=obj_item)

    return dst_bucket_objects
