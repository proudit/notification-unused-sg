import boto3
import datetime
import os
import pandas as pd
import json
import uuid

# 環境変数
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "棚卸し")  # 環境変数でカスタマイズ可能に
ENV = os.getenv("ENV")
SNS_TOPIC_ARN = os.getenv("SNS_TOPIC_ARN")

def get_unattached_sgs():
    ec2 = boto3.client("ec2")
    sgs = ec2.describe_security_groups()["SecurityGroups"]
    network_interfaces = ec2.describe_network_interfaces()["NetworkInterfaces"]

    # アタッチされているSGを取得
    attached_sgs = {sg["GroupId"] for ni in network_interfaces for sg in ni.get("Groups", [])}

    # どこにもアタッチされていないSGをフィルタリング
    unused_sgs = [sg for sg in sgs if sg["GroupId"] not in attached_sgs]

    # SG情報整理
    sg_list = []
    for sg in unused_sgs:
        tags = {t["Key"]: t["Value"] for t in sg.get("Tags", [])}
        sg_list.append({
            "SG名": sg.get("GroupName", "N/A"),
            "Nameタグ": tags.get("Name", "N/A"),
            "SG ID": sg.get("GroupId", "N/A"),
            "Teamタグ": tags.get("Team", "N/A"),
            "Descriptionタグ": sg.get("Description", "N/A"),
        })

    return sg_list

def save_to_s3(data):
    df = pd.DataFrame(data)
    date_str = datetime.datetime.now().strftime("%Y-%m-%d")
    file_name = f"{S3_PREFIX}/{date_str}-{ENV}-Unused-SG.xlsx"  # プレフィックスを環境変数化
    s3_path = f"s3://{S3_BUCKET}/{file_name}"
    s3_url = f"https://s3.console.aws.amazon.com/s3/object/{S3_BUCKET}?prefix={file_name}"

    # Excelを一時ファイルに保存
    temp_file = "/tmp/unused_sg.xlsx"
    with pd.ExcelWriter(temp_file) as writer:
        df.to_excel(writer, index=False)

    # S3へアップロード
    s3 = boto3.client("s3")
    s3.upload_file(temp_file, S3_BUCKET, file_name)

    return file_name, s3_url

def send_sns_notification(sg_count, s3_url):
    sns = boto3.client("sns")

    # AWS Chatbot に適したメッセージフォーマット
    message_body = {
        "version": "1.0",
        "source": "custom",
        "id": str(uuid.uuid4()),  # 一意のIDを生成
        "content": {
            "textType": "client-markdown",
            "title": f":information_source: [{ENV}] 未使用の Security Group リストアップデート",
            "description": (
                f"*対象環境:* *{ENV}*\n"
                f"*検出されたSG数:* *{sg_count}*\n"
                f"*S3ダウンロードリンク:* <{s3_url}|ダウンロード>"
            ),
            "nextSteps": [
                "セキュリティグループの要不要を確認してください。",
                "<https://console.aws.amazon.com/ec2/v2/home?#SecurityGroups|AWS EC2 セキュリティグループ管理画面>"
            ]
        }
    }

    # SNS に送信する JSON メッセージ
    message = json.dumps({
        "default": json.dumps(message_body)  # "default" フィールドを必須にする
    })

    # SNS にメッセージを送信
    sns.publish(
        TopicArn=SNS_TOPIC_ARN,
        Message=message,
        MessageStructure="json"
    )

def lambda_handler(event, context):
    sg_data = get_unattached_sgs()
    sg_count = len(sg_data)

    if sg_count == 0:
        print("未使用のSecurity Groupはありません。")
        return

    s3_key, s3_url = save_to_s3(sg_data)
    send_sns_notification(sg_count, s3_url)

    return {"status": "success", "file": s3_key, "sg_count": sg_count}
