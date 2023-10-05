# Parittion S3 Replicate

This is a Lambda function to replicate an object between buckets in different
AWS partitions. Because there is no trust relationship between partitions, the
normal S3 Replication process cannot be used.

This solution uses an SQS FIFO Queue to process events for the same object in
the order they are received. However, there is the chance that if an object is
quickly modified then the events might arrive to the queue out of order. You
should be cautious about using this when the same object is being modified
quickly.

## buildspec.yml

CodeBuild uses this to call the `Makefile` to build the Lambda and upload its
build artifact (a zip for deploying Lambda) to S3. If you want to customize
something about the build process then you should likely be editing the
`Makefile` instead.

### Variables

The pipeline and CodeBuild set several custom variables to control the
CodeBuild process. You do not need to change or set these values when cerating
a Lambda, they are just provided for documentation:

| Name               | Default        | Description |
| ------------------ | -------------- | ----------- |
| ENVIRONMENT        |                | The type of build being performed: prod, dev, test, qa, etc. If not specified then the build artifact will not be uploaded. |
| PACKAGE_BUCKET     |                | The S3 bucket name to place the build artifact in. If not specified then the build artifact will not be uploaded. |
| PACKAGE_PREFIX     |                | An optional prefix to use when uploading the build artifact to S3. If specified this must not begin with a `/` and must end with a `/`. It is appended in addition to the app name specified in the buildspec. |
| PACKAGE_KMS_KEY_ID | `alias/aws/s3` | The AWS KMS Key ID (alias name, ID, or ARN) to use for encrypting the build artifact in S3. |

Variables are also exported from CodeBuild:

| Name               | Description |
| ------------------ | ----------- |
| PACKAGE_BUILD_HASH | Unique hash created from the hash of all source code files and dependencies used in the build. |
| PACKAGE_KEY        | The S3 Object Key for the hash version of the build artifact. |

## Makefile

This is the main way to control the build process for the Lambda. It has a
couple top level targets, and then several utility targets.

### clean

Removes all of the build artifact directories. This should return the Lambda
directory to when it was checked out.

### build

Builds the Lambda by installing the dependencies and copying the source code
to the build directory (usually `build/`).

### lint

Run pylint on the built Lambda. Lint errors and warnings should be corrected,
or individually disabled in the code if they are reviewed and not an issue.
Running pylint before a checkin can help find many common, subtle errors.

Module, class, and function docstrings should follow the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html#s3.8-comments-and-docstrings).

### test

Run pytest from the `tests/` directory on the built Lambda. The majority of
the Lambda functions and classes should be covered by comprehensive unit tests.
Running pytest before a checkin is SOP, and pipeline builds will fail if a
Lambda's tests fail.

### validate

Run `terraform validate` from the `terraform/` directory. It will catch basic
errors in the terraform, but many classes of error slip through.

### dist

Take the built Lambda and package it in a zip for deployment. This produces an
artifact in the dist directory (usually `dist/`). Any Lambda built and deployed
for the project must be built on Linux (macOS and Windows builds will sometimes
not work in AWS Lambda).

### internal targets

Some of the make targets are for internal use:

- **.lint-setup:** install requirements for pylint.
- **.test-setup:** install requirements for pytest.
- **.validate-setup:** install requirements for terraform validate.
- **lint-report:** run pylint and output a JUnit XML.
- **test-report:** run pytest and output a JUnit XML.
