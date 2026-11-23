"""命令行入口：编目、核订邮路、核证展稿与换展分析。

所有子命令只读取本地资料文件，默认使用仓库内 data/records.json，
也可用 --data 指向其他目录中的合成资料。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .catalog import load_catalog
from .engine import CoverVerification, verify_all
from .exhibit import (
    adjudicate_all,
    analyze_swap,
    build_label,
)
from .parse import all_mark_facts

DEFAULT_DATA = Path(__file__).resolve().parents[1] / 'data' / 'records.json'


def _to_plain(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat(sep=' ')
    if isinstance(obj, date):
        return obj.isoformat()
    if is_dataclass(obj):
        return {key: _to_plain(value) for key, value in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _to_plain(value) for key, value in obj.items()}
    if isinstance(obj, (frozenset, set)):
        return sorted(obj)
    return obj


# ---------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------

def cmd_catalog(args: argparse.Namespace) -> int:
    catalog, era = load_catalog(args.data)
    facts_by_mark = all_mark_facts(catalog)
    output: dict[str, Any] = {
        'era': {
            'era_years': era.era_years,
            'issue_dates': {key: value.isoformat() for key, value in era.issue_dates.items()},
        },
        'covers': [],
    }
    for cover_id, cover in catalog.covers.items():
        cover_block: dict[str, Any] = {
            'cover': cover_id,
            'image': cover.image,
            'insert': cover.insert,
            'note': cover.note,
            'marks': [],
        }
        for mark in catalog.marks_of(cover_id):
            facts = facts_by_mark[mark.id]
            mark_block = {
                'mark': mark.id,
                'side': mark.side,
                'order_hint': mark.order_hint,
                'readings': [],
                'flags': {flag: sources for flag, sources in facts.flags.items()},
                'uncertain_fields': sorted(facts.uncertain_fields),
                'office_token_candidates': {
                    token: sources for token, sources in facts.office_candidates.items()
                },
                'month_day_candidates': {
                    f'{month:02d}-{day:02d}': sources
                    for (month, day), sources in facts.month_day_candidates.items()
                },
                'hour_candidates': {
                    hour: sources for hour, sources in facts.hour_candidates.items()
                },
                'non_delivery_mark': facts.is_non_delivery,
                'overlap_candidate': facts.has_overlap,
                'return_note': facts.has_return_note,
                'parse_issues': facts.parse_issues,
            }
            for parsed in facts.parsed:
                reading = parsed.reading
                mark_block['readings'].append({
                    'id': reading.id,
                    'reader': reading.reader,
                    'raw_text': reading.text,
                    'certainty': reading.certainty,
                    'date': parsed.date_expr.describe() if parsed.date_expr else None,
                    'office_token': parsed.office_token,
                    'flags': sorted(parsed.flags),
                    'uncertain_fields': sorted(parsed.uncertain_fields),
                    'delivery_evidence': parsed.is_delivery_evidence,
                })
            cover_block['marks'].append(mark_block)
        output['covers'].append(cover_block)

    if args.json:
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    print('馆内年表：')
    for token, year in era.era_years.items():
        print(f'  {token} = {year}')
    for image, day in era.issue_dates.items():
        print(f'  {image} 启用日 {day.isoformat()}')
    for block in output['covers']:
        print(f"\n封 {block['cover']}｜{block['image']}｜展笺：{block['insert']}")
        for mark in block['marks']:
            print(f"  戳 {mark['mark']}（{mark['side']}面，叠印次序 {mark['order_hint']}）")
            for reading in mark['readings']:
                flags = ('，标记：' + '、'.join(reading['flags'])) if reading['flags'] else ''
                evidence = '' if reading['delivery_evidence'] else '，不作递送证据'
                print(
                    f"    [{reading['id']}] {reading['reader']}（{reading['certainty']}）："
                    f"{reading['raw_text']} -> {reading['date']}，"
                    f"局名称谓={reading['office_token']}{flags}{evidence}"
                )
            if mark['uncertain_fields']:
                print(f"    存疑字段：{'、'.join(mark['uncertain_fields'])}（不按票数定局）")
            if mark['parse_issues']:
                for issue in mark['parse_issues']:
                    print(f"    解析问题：{issue}")
    return 0


# ---------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------

def _print_verification(verification: CoverVerification, catalog) -> None:
    print(f"\n=== 封 {verification.cover_id} 核订 ===")
    if not verification.is_feasible:
        for issue in verification.contradictions:
            print(f'  矛盾：{issue}')
        return
    print(f'  可行解释数：{len(verification.feasible)}（枚举全部，不取其一消除矛盾）')
    print('  已核实戳迹（所有可行解释一致）：')
    for mark in verification.confirmed_marks:
        print(f"    {mark['mark']}：{'；'.join(mark['alternatives'])}")
    if verification.disputed_marks:
        print('  分歧戳迹：')
        for mark in verification.disputed_marks:
            print(f"    {mark['mark']}：{' ／ '.join(mark['alternatives'])}")
    print('  已核实路段：')
    for edge in verification.confirmed_edges:
        print(f"    {edge['route']} {edge['from']}->{edge['to']}")
    if verification.disputed_edges:
        print('  分歧路段：')
        for edge in verification.disputed_edges:
            print(f"    {edge['route']} {edge['from']}->{edge['to']}")
    print(f"  退回结构：{verification.return_status}")
    print(f"  首日实寄：{verification.first_day_status} —— {verification.first_day_reason}")
    for guard in verification.transit_guards:
        print(f"  通达时间护栏：{guard.from_mark} 与 {guard.to_mark} 跨趟，{guard.reason}")
    for pivot in verification.pivots:
        print(
            f"  复查点 {pivot.mark_id}（{pivot.field}）："
            f"{' ／ '.join(pivot.alternatives)}；改读影响：{pivot.effect}"
        )
    for note in verification.physical_notes:
        print(
            f"  实物说明：{note['mark']} 带“{note['flag']}”，"
            '排除递送证据资格，保留物件说明'
        )


def cmd_verify(args: argparse.Namespace) -> int:
    catalog, era = load_catalog(args.data)
    verdicts = verify_all(catalog, era)
    targets = [args.cover] if args.cover else catalog.covers.keys()
    if args.json:
        payload = {cover_id: _to_plain(verdicts[cover_id]) for cover_id in targets}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for cover_id in targets:
        if cover_id not in verdicts:
            print(f'未知封件：{cover_id}')
            return 2
        _print_verification(verdicts[cover_id], catalog)
    return 0


# ---------------------------------------------------------------------
# exhibit
# ---------------------------------------------------------------------

def cmd_exhibit(args: argparse.Namespace) -> int:
    catalog, era = load_catalog(args.data)
    verdicts, display_verdicts = adjudicate_all(catalog, era)
    targets = [args.display] if args.display else catalog.displays.keys()
    if args.json:
        payload = {display_id: _to_plain(display_verdicts[display_id])
                   for display_id in targets}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for display_id in targets:
        verdict = display_verdicts[display_id]
        print(f"\n=== 展陈 {display_id}（{catalog.displays[display_id].time}）===")
        print(f'草稿：{verdict.draft}')
        print(f'总裁决：{verdict.overall}（年代冲突：{verdict.era_conflict}）')
        for finding in verdict.findings:
            print(f"  [{finding.status}] 封{finding.cover_id} {finding.claim_text}：{finding.detail}")
        if verdict.blockers:
            print('  阻断原因：')
            for blocker in verdict.blockers:
                extra = ''
                if blocker.reading_sources:
                    extra = f"（释读 {','.join(blocker.reading_sources)}）"
                print(f"    - 封{blocker.cover_id} {blocker.kind}：{blocker.detail}{extra}")
    return 0


# ---------------------------------------------------------------------
# label
# ---------------------------------------------------------------------

def cmd_label(args: argparse.Namespace) -> int:
    catalog, era = load_catalog(args.data)
    verdicts = verify_all(catalog, era)
    if args.cover not in verdicts:
        print(f'未知封件：{args.cover}')
        return 2
    label = build_label(catalog, verdicts[args.cover])
    if args.json:
        print(json.dumps(_to_plain(label), ensure_ascii=False, indent=2))
        return 0
    print(f"扫码说明｜封 {label.cover_id}（{label.image}）")
    print('【已核实的递送】')
    for line in label.verified or ['（无）']:
        print(f'  - {line}')
    print('【题签上的说法】')
    for line in label.insert_claims:
        print(f'  - {line}')
    print(f'  - {label.first_day_line}')
    print('【未知段与复查点】')
    for line in label.unknown or ['（无）']:
        print(f'  - {line}')
    print('【实物说明（不作递送证据）】')
    for line in label.physical_notes or ['（无）']:
        print(f'  - {line}')
    return 0


# ---------------------------------------------------------------------
# swap
# ---------------------------------------------------------------------

def cmd_swap(args: argparse.Namespace) -> int:
    catalog, era = load_catalog(args.data)
    verdicts, _ = adjudicate_all(catalog, era)
    if args.display not in catalog.displays:
        print(f'未知展陈：{args.display}')
        return 2
    for cover_id in (args.out, args.into):
        if cover_id not in catalog.covers:
            print(f'未知封件：{cover_id}')
            return 2
    reports = analyze_swap(
        catalog, catalog.displays[args.display], args.out, args.into, verdicts
    )
    if args.json:
        print(json.dumps([_to_plain(report) for report in reports],
                         ensure_ascii=False, indent=2))
        return 0
    print(f"换展：{args.display} 取出 {args.out}，换入 {args.into}")
    for report in reports:
        mark = '（状态变化）' if report.changed else '（状态不变）'
        print(
            f"\n说法「{report.claim_text}」{mark}："
            f"{report.status_before} -> {report.status_after}"
        )
        if report.blockers:
            print('  使说法悬置的封件/释读/年代限制：')
            for blocker in report.blockers:
                extra = ''
                if blocker.reading_sources:
                    extra = f"，释读 {','.join(blocker.reading_sources)}"
                if blocker.route_id:
                    extra += f"，路段 {blocker.route_id}"
                print(f"    - 封{blocker.cover_id} {blocker.kind}：{blocker.detail}{extra}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='posthist',
        description='邮史封件编目、邮路核订与展稿核证（本地合成资料）',
    )
    parser.add_argument('--data', type=Path, default=DEFAULT_DATA,
                        help='records.json 路径，默认仓库内 data/records.json')
    sub = parser.add_subparsers(dest='command', required=True)

    p_catalog = sub.add_parser('catalog', help='列出结构化编目与戳迹释读')
    p_catalog.add_argument('--json', action='store_true')
    p_catalog.set_defaults(func=cmd_catalog)

    p_verify = sub.add_parser('verify', help='核订单件或全部封件的可行递送解释')
    p_verify.add_argument('--cover')
    p_verify.add_argument('--json', action='store_true')
    p_verify.set_defaults(func=cmd_verify)

    p_exhibit = sub.add_parser('exhibit', help='核证展陈草稿与多件互证')
    p_exhibit.add_argument('--display')
    p_exhibit.add_argument('--json', action='store_true')
    p_exhibit.set_defaults(func=cmd_exhibit)

    p_label = sub.add_parser('label', help='生成单件封的扫码展签稿')
    p_label.add_argument('--cover', required=True)
    p_label.add_argument('--json', action='store_true')
    p_label.set_defaults(func=cmd_label)

    p_swap = sub.add_parser('swap', help='换展后逐说法比较裁决变化')
    p_swap.add_argument('--display', required=True)
    p_swap.add_argument('--out', required=True)
    p_swap.add_argument('--into', required=True)
    p_swap.add_argument('--json', action='store_true')
    p_swap.set_defaults(func=cmd_swap)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
