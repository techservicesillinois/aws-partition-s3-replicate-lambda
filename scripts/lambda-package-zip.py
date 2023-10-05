#!/usr/bin/env python3
"""
Takes a AWS Lambda or Lambda Layer build directory and creates a zip package
from it. It uses the hash from this package to determine if it has differences
than the version in S3, and should be uploaded:

- Libraries and executables are hashed after being stripped of their symbols.
  This ensures that compiler metadata doesn't needlessly change the hash.
- *.pyc, *.pyi, *.o files are skipped in the hash.
- Makefile files are skipped in the hash.
- __pycache__ and *.dist-info directories and their contents are skipped in the
  hash.
- symlinks have their target hashed.
- All other files are hashed as is.

The command line options let you upload to a single destination, but you can
upload to multiple at once with the PACKAGE_X_BUCKET env variables. It will
upload a zip named with the hash and one with the environment (if specified)
for each destination. The objects contain this metadata:

- package-hash
- commit-hash
"""
from argparse import ArgumentParser, FileType
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import logging
import os
from os import path
import re
from shutil import copyfileobj
import stat
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import boto3
from botocore.exceptions import ClientError
import git
import magic

DEFAULT_REGION = os.environ.get(
    'AWS_REGION',
    os.environ.get(
        'AWS_DEFAULT_REGION',
        'us-east-2'
    )
)

DEFAULT_APP_NAME = os.environ.get('APP_NAME')
DEFAULT_ENVIRONMENT = os.environ.get('ENVIRONMENT', 'latest')
DEFAULT_PACKAGE_BUCKET = os.environ.get('PACKAGE_BUCKET')
DEFAULT_PACKAGE_PREFIX = os.environ.get('PACKAGE_PREFIX', '')
DEFAULT_PACKAGE_NAMES = os.environ.get('PACKAGE_NAMES', 'hash').split(',')
DEFAULT_PACKAGE_KMS_KEY_ID = os.environ.get('PACKAGE_KMS_KEY_ID', 'alias/aws/s3')
DEFAULT_PACKAGE_REGION = os.environ.get('PACKAGE_REGION', DEFAULT_REGION)

ENV_PACKAGE_BUCKET_RE = re.compile(r'^PACKAGE_(?P<idx>\d+)_BUCKET$')
LIBRARY_MIMETYPES = [
    'application/x-archive',
    'application/x-sharedlib',
    'application/x-executable',
    'application/x-mach-binary',
]
SKIP_FILE_EXTS = {'.pyc', '.pyi', '.o'}
SKIP_FILE_NAMES = {
    'Makefile',
}
SKIP_DIRS = {
    'bin',
    'python/bin',
    'node_modules/.bin',
}

def _tmpdir():
    for name in ['TMPDIR', 'TEMP', 'TMP']:
        if value := os.environ.get(name):
            return value
    return '/tmp'
TMPDIR = _tmpdir()

PackageDestination = namedtuple(
    'PackageDestination',
    field_names=['bucket', 'prefix', 'kms_key_id', 'region'],
    defaults=[DEFAULT_PACKAGE_PREFIX, DEFAULT_PACKAGE_KMS_KEY_ID, DEFAULT_PACKAGE_REGION]
)

logger = logging.getLogger(__name__)

class StripError(Exception):
    """ Raised when the strip process returns non-success. """
    def __init__(self, file_path, returncode, output):
        super().__init__(f"strip {file_path} (exit={returncode}): {output}")
        self.file_path = file_path
        self.returncode = returncode
        self.output = output

def get_args():
    """ Get the command line arguments. """
    parser = ArgumentParser(description='Creates a Lambda zip archive from a build directory, then uploads it to S3.')
    parser.add_argument(
        '--debug', '-d',
        action='store_true',
        help='Enable debug logging.'
    )

    parser.add_argument(
        '--app', '-a',
        metavar='NAME',
        dest='app',
        default=DEFAULT_APP_NAME,
        required=True,
        help='The application name for the archive. Default: %(default)r'
    )
    parser.add_argument(
        '--env', '-e',
        dest='environment',
        default=DEFAULT_ENVIRONMENT,
        help='Name of the environment (test, dev, prod, etc). Default: %(default)r'
    )
    parser.add_argument(
        '--bucket', '-b',
        default=DEFAULT_PACKAGE_BUCKET,
        help='The S3 bucket to upload to. Default: %(default)r'
    )
    parser.add_argument(
        '--prefix', '-p',
        default=DEFAULT_PACKAGE_PREFIX,
        help='The S3 object key prefix to use. Default: %(default)r'
    )
    parser.add_argument(
        '--names',
        metavar='NAME',
        choices=['hash', 'commit'],
        nargs='+',
        default=DEFAULT_PACKAGE_NAMES,
        help='Names to upload to S3, excluding the environment name. Default: %(default)r'
    )
    parser.add_argument(
        '--kms-key-id', '-k',
        metavar='ALIAS|ARN|ID',
        default=DEFAULT_PACKAGE_KMS_KEY_ID,
        help='The KMS Key to encrypt the artifact with. Default: %(default)r'
    )
    parser.add_argument(
        '--region', '-r',
        default=DEFAULT_PACKAGE_REGION,
        help='The region the bucket is in. Default: %(default)r'
    )

    parser.add_argument(
        '--output', '-o',
        type=FileType('wb'),
        help='Write an output file file.'
    )
    parser.add_argument(
        'path',
        help='The location to get the artifacts from.'
    )

    return parser.parse_args()

def main(args):
    """
    Gather the destinations from args and env, calculate the package hash,
    build the package zip, and upload it to S3.
    """
    if args.debug:
        logger.setLevel(logging.DEBUG)

    dests = []
    if args.bucket:
        if args.prefix != '' and not args.prefix.endswith('/'):
            raise ValueError('Argument --prefix must end with "/" or be empty')
        dest = PackageDestination(args.bucket, args.prefix, args.kms_key_id, args.region)
        logger.debug(
            'Adding destination: %(dest)r',
            {'dest': dest}
        )
        dests.append(dest)

    for name, value in os.environ.items():
        if match := ENV_PACKAGE_BUCKET_RE.match(name):
            idx = match.group('idx')
            dest_prefix = os.environ.get(f"PACKAGE_{idx}_PREFIX", DEFAULT_PACKAGE_PREFIX)
            if dest_prefix != '' and not dest_prefix.endswith('/'):
                raise ValueError(f"Variable PACKAGE_{idx}_PREFIX must end with \"/\" or be empty")

            dest = PackageDestination(
                value,
                dest_prefix,
                os.environ.get(f"PACKAGE_{idx}_KMS_KEY_ID", DEFAULT_PACKAGE_KMS_KEY_ID),
                os.environ.get(f"PACKAGE_{idx}_REGION", DEFAULT_PACKAGE_REGION)
            )

            logger.debug(
                'Adding destination: %(dest)r',
                {'dest': dest}
            )
            dests.append(dest)

    if not dests:
        logger.info('No destinations.')
        return 0

    commit_hash = None
    try:
        repo = git.Repo(args.path, search_parent_directories=True)
    except git.InvalidGitRepositoryError:
        pass
    else:
        commit_hash = repo.head.object.hexsha

    s3_clnts = {}
    for dest in dests:
        if dest.region in s3_clnts:
            continue
        s3_clnts[dest.region] = boto3.client('s3', region_name=dest.region)

    has_errors = False
    package_hash = get_package_hash(args.path)
    with NamedTemporaryFile(prefix=f"{args.app}-", suffix='.zip', dir=TMPDIR, mode='w+b') as package_zip:
        make_package_zip(args.path, package_zip)

        with ThreadPoolExecutor() as executor:
            future2upload = {}
            for dest in dests:
                file_keys = []
                for name in args.names:
                    if name == 'hash':
                        file_keys.append(f"{package_hash}.zip")
                    elif name == 'commit':
                        file_keys.append(f"commit-{commit_hash[0:7]}.zip")
                if args.environment:
                    file_keys.append(f"{args.environment}.zip")

                for file_key in file_keys:
                    dest_key = dest.prefix + args.app + '/' + file_key
                    future = executor.submit(
                        upload_package,
                        file_path=package_zip.name,
                        bucket=dest.bucket,
                        key=dest_key,
                        kms_key_id=dest.kms_key_id,
                        package_hash=package_hash,
                        commit_hash=commit_hash,
                        s3_clnt=s3_clnts[dest.region],
                    )
                    future2upload[future] = dest, file_key

            for future in as_completed(future2upload.keys()):
                dest, dest_key = future2upload[future]
                try:
                    future.result()
                except Exception:
                    logger.exception(
                        'Unable to upload to s3://%(bucket)s/%(key)s',
                        {'bucket': dest.bucket, 'key': dest_key}
                    )
                    has_errors = True

        if args.output:
            logger.info('Copying to the output file')
            package_zip.seek(0, os.SEEK_SET)
            copyfileobj(package_zip, args.output)

    return 1 if has_errors else 0

def make_package_zip(package_path, package_zip):
    """
    Make the package zip file. This adds all the links and regular files to the
    package_zip from package_path.

    Args:
        package_path (str): the build directory of the package.
        package_zip (File-line): the open file to write the zip to.
    """
    _logger = logger.getChild(f"make_package_zip({package_path})")
    with ZipFile(package_zip, 'w', ZIP_DEFLATED) as archive:
        for root, _, files in os.walk(package_path):
            for file_name in files:
                file_path = path.join(root, file_name)
                file_rel = path.relpath(file_path, package_path)

                file_st = os.stat(file_path, follow_symlinks=False)
                if stat.S_ISLNK(file_st.st_mode):
                    # Need to create a ZipInfo object manually, and populate
                    # it with the correct file st_mode options. The content
                    # is the content of the link.
                    file_info = ZipInfo(
                        file_rel,
                        time.localtime(file_st.st_mtime)[0:6]
                    )
                    file_info.create_system = 3
                    file_info.external_attr = file_st.st_mode << 16
                    archive.writestr(file_info, os.readlink(file_path))

                elif stat.S_ISREG(file_st.st_mode):
                    # Regular file, just write it out
                    archive.write(file_path, file_rel)

                else:
                    _logger.warning(
                        '%(file)s: unknown type 0x%(mode)08x',
                        {'file': file_rel, 'mode': file_st.st_mode}
                    )

    package_zip.flush()

def _get_package_hash_file(file_path):
    """ Get the contents of a file, in chunks. """
    with open(file_path, 'rb') as file_p:
        yield from iter(lambda: file_p.read(1024*8), b'')

def _get_package_hash_lib(file_path):
    """ Get the contents of a library file, stripping out symbols. """
    file_name, file_ext = path.splitext(path.basename(file_path))
    with NamedTemporaryFile(prefix=f"{file_name}-", suffix=file_ext, dir=TMPDIR, mode='w+b') as stripped:
        if sys.platform.startswith('linux'):
            proc_cmd = ['strip', '--remove-section=.note.gnu.build-id', '--strip-all', '-o', stripped.name, file_path]
        elif sys.platform.startswith('darwin'):
            proc_cmd = ['strip', '-x', '-o', stripped.name, file_path]

        proc = subprocess.run(
            proc_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            text=True,
        )
        if proc.returncode != 0:
            raise StripError(file_path, proc.returncode, proc.stdout)

        yield from iter(lambda: stripped.read(1028*8), b'')

def get_package_hash(package_path):
    """
    Get the hash of the package build directory contents. See the module note
    for what is included in the hash value and how.

    Args:
        package_path (str): path to the build directory.

    Returns:
        str: sha256 of the contents.
    """
    _logger = logger.getChild(f"package_hash({package_path})")
    hasher = sha256()
    for root, dirs, files in os.walk(package_path):
        # Loop over files as sorted list, so the hash is stable.
        for file in sorted(files):
            if file in SKIP_FILE_NAMES:
                continue
            _, file_ext = path.splitext(file)
            # File extensions we don't care about
            if file_ext in SKIP_FILE_EXTS:
                continue

            file_path = path.join(root, file)
            file_rel = path.relpath(file_path, package_path)

            # Always add the file path to the hash, in case a file is renamed.
            hasher.update(file_rel.encode('utf-8'))
            file_st = os.stat(file_path)
            if stat.S_ISLNK(file_st.st_mode):
                # For links, add the content of the link itself and not the
                # file it points to.
                _logger.debug('%(file)s: link', {'file': file_rel})
                hasher.update(os.readlink(file_path).encode('utf-8'))

            elif stat.S_ISREG(file_st.st_mode):
                # For regular files, determine if it is a library (and should
                # be stripped of symbols) or not.
                if magic.from_file(file_path, mime=True) in LIBRARY_MIMETYPES:
                    _logger.debug('%(file)s: library', {'file': file_rel})
                    stream = _get_package_hash_lib(path.join(root, file))
                else:
                    stream = _get_package_hash_file(path.join(root, file))

                for data in stream:
                    hasher.update(data)

            else:
                _logger.warning(
                    '%(file)s: unknown type 0x%(mode)08x',
                    {'file': file_rel, 'mode': file_st.st_mode}
                )

        _dirs = []
        # Read the child dirs as a sorted list, to make the hash stable. Also
        # ignore some dirs we don't care about.
        for _dir in sorted(dirs):
            if _dir.endswith('.dist-info'):
                continue
            if _dir == '__pycache__':
                continue

            _dir_rel = path.relpath(path.join(root, _dir), package_path)
            if _dir_rel in SKIP_DIRS:
                continue

            _dirs.append(_dir)
        dirs[:] = _dirs

    package_hash = hasher.hexdigest()

    _logger.info(
        'Computed hash: %(hash)r',
        {'hash': package_hash}
    )
    return package_hash

def upload_package(file_path, bucket, key, kms_key_id, package_hash, commit_hash, s3_clnt):
    """
    Uploads the package zip to S3. This tries to detect if the object exists
    with the same package-hash already, by looking for the metadata. If not
    then it will upload a new version.

    Args:
        file_path (str): path to the zip file.
        bucket (str): destination bucket.
        key (str): destination key.
        kms_key_id (str): KMS encryption key.
        package_hash (str): the content hash.
        commit_hash (str): the repo commit.
        s3_clnt (obj): boto3 client for the destination in its region.
    """
    _logger = logger.getChild(f"upload_package(s3://{bucket}/{key})")

    current_package_hash = None
    # Try to get the current package hash from an S3 object, if one exists. It
    # is stored in the 'package-hash' metadata field.
    try:
        res = s3_clnt.head_object(Bucket=bucket, Key=key)
    except ClientError as client_err:
        if client_err.response['Error']['Code'] not in ['NotFound', 'NoSuchKey', '404']:
            _logger.info('Error: %(error)r', {'error': client_err.response['Error']})
            raise
        _logger.debug('No existing package found.')
    else:
        current_package_hash = res['Metadata'].get('package-hash')

    if current_package_hash == package_hash:
        _logger.info('Current package matches hash value.')
        return

    metadata = {
        'package-hash': package_hash,
    }
    if commit_hash:
        metadata['commit-hash'] = commit_hash

    _logger.info('Uploading %(file_path)s', {'file_path': file_path})
    s3_clnt.upload_file(
        Filename=file_path,
        Bucket=bucket,
        Key=key,
        ExtraArgs={
            'ContentType': 'application/zip',
            'Metadata': metadata,
            'ServerSideEncryption': 'aws:kms',
            'SSEKMSKeyId': kms_key_id,
        }
    )


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
    )
    sys.exit(main(get_args()))
