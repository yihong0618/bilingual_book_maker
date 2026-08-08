"""Create (or recreate) a Google Cloud Translation v3 glossary from a CSV term file.

The CSV is an unidirectional glossary: two columns per line, source term
then target term, no header. Example (en -> zh-CN):

    account,账户
    Neural Machine Translation,神经机器翻译

Usage:

    # from a local CSV (uploaded to GCS automatically)
    python scripts/create_glossary.py \
        --project_id my-proj --glossary_id my-terms \
        --source_lang en --target_lang zh-CN \
        --csv ./terms.csv --gcs_bucket my-bucket

    # from a CSV already in GCS
    python scripts/create_glossary.py \
        --project_id my-proj --glossary_id my-terms \
        --source_lang en --target_lang zh-CN \
        --csv gs://my-bucket/glossaries/my-terms.csv

Authentication uses Application Default Credentials:
    export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
or
    gcloud auth application-default login

The account needs roles/cloudtranslate.editor, plus
roles/storage.objectAdmin when uploading a local CSV.
"""

import argparse
import sys

ADC_HELP = """Google Cloud Application Default Credentials (ADC) not found.
Set up one of the following, then retry:
  1. export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
  2. gcloud auth application-default login"""


def upload_csv_to_gcs(bucket_name, glossary_id, csv_path):
    try:
        from google.cloud import storage
    except ImportError:
        sys.exit(
            "google-cloud-storage is required to upload a local CSV.\n"
            "Install it with: pip install google-cloud-storage\n"
            "(or upload the CSV yourself and pass a gs:// URI to --csv)"
        )
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    if not bucket.exists():
        sys.exit(
            f"GCS bucket '{bucket_name}' not found. Create it first, e.g.:\n"
            f"  gcloud storage buckets create gs://{bucket_name} --location=us-central1"
        )
    blob_name = f"glossaries/{glossary_id}.csv"
    bucket.blob(blob_name).upload_from_filename(csv_path)
    uri = f"gs://{bucket_name}/{blob_name}"
    print(f"Uploaded {csv_path} -> {uri}")
    return uri


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project_id", required=True)
    parser.add_argument("--glossary_id", required=True)
    parser.add_argument(
        "--source_lang", required=True, help="BCP-47 code, e.g. en"
    )
    parser.add_argument(
        "--target_lang", required=True, help="BCP-47 code, e.g. zh-CN"
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="local CSV path (needs --gcs_bucket) or a gs:// URI",
    )
    parser.add_argument(
        "--gcs_bucket", help="GCS bucket to upload a local CSV into"
    )
    parser.add_argument(
        "--location",
        default="us-central1",
        help="glossaries are only supported in us-central1",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="delete and recreate the glossary if it already exists",
    )
    args = parser.parse_args()

    try:
        from google.cloud import translate_v3
    except ImportError:
        sys.exit(
            "google-cloud-translate is required.\n"
            "Install it with: pip install google-cloud-translate"
        )
    from google.api_core.exceptions import AlreadyExists, NotFound
    from google.auth.exceptions import DefaultCredentialsError

    if args.csv.startswith("gs://"):
        input_uri = args.csv
    else:
        if not args.gcs_bucket:
            sys.exit("--gcs_bucket is required when --csv is a local file")
        input_uri = upload_csv_to_gcs(
            args.gcs_bucket, args.glossary_id, args.csv
        )

    import os

    try:
        # REST by default: gRPC bypasses SOCKS/HTTP proxies and often
        # fails behind them, while REST honors standard proxy env vars.
        transport = os.environ.get("BBM_GOOGLE_TRANSPORT", "rest")
        client = translate_v3.TranslationServiceClient(transport=transport)
    except DefaultCredentialsError:
        sys.exit(ADC_HELP)

    parent = f"projects/{args.project_id}/locations/{args.location}"
    glossary_name = client.glossary_path(
        args.project_id, args.location, args.glossary_id
    )
    glossary = translate_v3.Glossary(
        name=glossary_name,
        language_pair=translate_v3.Glossary.LanguageCodePair(
            source_language_code=args.source_lang,
            target_language_code=args.target_lang,
        ),
        input_config=translate_v3.GlossaryInputConfig(
            gcs_source=translate_v3.GcsSource(input_uri=input_uri)
        ),
    )

    if args.force:
        try:
            print(f"Deleting existing glossary {glossary_name} ...")
            client.delete_glossary(name=glossary_name).result(timeout=300)
        except NotFound:
            pass

    try:
        operation = client.create_glossary(parent=parent, glossary=glossary)
        result = operation.result(timeout=300)
    except AlreadyExists:
        sys.exit(
            f"Glossary '{args.glossary_id}' already exists. "
            "Rerun with --force to delete and recreate it "
            "(v3 glossaries cannot be updated in place)."
        )
    except DefaultCredentialsError:
        sys.exit(ADC_HELP)

    print(f"Created glossary: {result.name}")
    print(f"Entries: {result.entry_count}")
    print(
        "Use it with:\n"
        f"  bbook_maker --model googlev3 --google_project_id {args.project_id} "
        f"--google_glossary_id {args.glossary_id} "
        f"--source_lang {args.source_lang} --language <target> "
        "--book_name <book>"
    )


if __name__ == "__main__":
    main()
