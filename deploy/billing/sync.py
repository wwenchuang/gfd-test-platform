"""Publish seven days of Beijing-time BSS daily bills, scoped to one product."""
import json
import os
import subprocess
import tempfile
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo


def daily(day, product, profile):
    token = ''
    seen = set()
    total = Decimal(0)
    count = 0
    while True:
        args = ['aliyun', 'bssopenapi', 'DescribeInstanceBill', '--profile', profile,
                '--BillingCycle', day[:7], '--BillingDate', day, '--Granularity', 'DAILY',
                '--PipCode', product, '--MaxResults', '300']
        if token:
            args.extend(['--NextToken', token])
        reply = json.loads(subprocess.check_output(args, timeout=40, stderr=subprocess.DEVNULL),
                           parse_float=Decimal)
        if reply.get('Code') != 'Success':
            raise ValueError('账单接口未成功')
        data = reply['Data']
        rows = data['Items']
        if isinstance(rows, dict):
            rows = rows['Item']
        for row in rows:
            if row['Currency'] != 'CNY' or row['BillingDate'] != day:
                raise ValueError('账单币种或日期不符')
            amount = Decimal(str(row['PretaxAmount']))
            if not amount.is_finite():
                raise ValueError('无效账单金额')
            total += amount
            count += 1
        token = data.get('NextToken')
        if not token:
            return dict(date=day, amount=str(total) if count else None, count=count)
        if token in seen:
            raise ValueError('账单分页重复')
        seen.add(token)


def main():
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    product = os.environ['BILLING_PIP_CODE']
    profile = os.environ['BILLING_CLI_PROFILE']
    if not product.strip() or not profile.strip():
        raise ValueError('账单配置为空')
    days = [daily((now.date()-timedelta(days=i)).isoformat(), product, profile) for i in range(7)]
    target = Path('/var/lib/midscene-billing/daily.json')
    target.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile('w', dir=target.parent, delete=False) as file:
        json.dump(dict(updated=now.isoformat(timespec='minutes'), days=days), file)
    os.chmod(file.name, 0o644)
    os.replace(file.name, target)


if __name__ == '__main__':
    main()
