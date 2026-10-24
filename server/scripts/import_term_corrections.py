"""용어 사전(term_corrections) CSV를 DB에 넣는다 (01-plan 3.4). 관리 화면이 생기기 전까지 이것으로 채운다.

사용: cd server && python -m scripts.import_term_corrections terms.csv [--replace] [--dry-run]

CSV(UTF-8, 엑셀이 붙이는 BOM 허용)의 머리글은 domain,wrong,correct 이다.
- domain: 도메인 이름(domain_keywords.domain_name). 그 이름의 도메인이 없거나 둘 이상이면 실패한다.
- wrong: 오인식 표현, correct: 정식 표기. 같은 도메인·wrong이 이미 있으면 correct를 새 값으로 바꾼다.
- --replace: 파일에 나온 도메인의 기존 항목을 모두 지우고 파일 내용으로 채운다(파일에서 뺀 항목을 DB에서도 지울 때).
- --dry-run: 검사하고 결과만 보여 준다. DB에 쓰지 않는다.
한 줄이라도 잘못되면 아무것도 쓰지 않는다. DB는 앱과 같은 DATABASE_URL을 쓴다.
"""
import argparse
import csv
import sys
from dataclasses import dataclass
from typing import Iterable

from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import insert

from database import DomainKeywords, SessionLocal, TermCorrection, init_db

COLUMNS = ("domain", "wrong", "correct")


@dataclass(frozen=True)
class Row:
    line: int
    domain: str
    wrong: str
    correct: str


class CsvImportError(Exception):
    """CSV나 도메인이 잘못됐다. 메시지에 줄 번호와 이유를 모두 담는다."""


def parse_rows(lines: Iterable[str]) -> list[Row]:
    reader = csv.DictReader(lines)
    if reader.fieldnames is None or [name.strip() for name in reader.fieldnames] != list(COLUMNS):
        raise CsvImportError(f"header must be exactly: {','.join(COLUMNS)} (got {reader.fieldnames})")
    rows, errors, seen = [], [], {}
    for record in reader:
        line = reader.line_num
        values = [(record.get(name) or "").strip() for name in reader.fieldnames]
        if not any(values):
            continue  # 빈 줄
        domain, wrong, correct = values
        if not (domain and wrong and correct):
            errors.append(f"line {line}: domain, wrong, correct must all be filled")
            continue
        if wrong == correct:
            errors.append(f"line {line}: wrong and correct are the same ({wrong!r})")
            continue
        key = (domain, wrong)
        if key in seen:
            if seen[key].correct != correct:
                errors.append(f"line {line}: {wrong!r} in {domain!r} already maps to {seen[key].correct!r} on line {seen[key].line}")
            continue
        seen[key] = Row(line, domain, wrong, correct)
        rows.append(seen[key])
    if errors:
        raise CsvImportError("\n".join(errors))
    return rows


def import_rows(rows: list[Row], *, replace: bool = False, dry_run: bool = False) -> dict:
    """rows를 DB에 넣고 {domains, inserted, updated, unchanged, deleted} 개수를 돌려준다."""
    db = SessionLocal()
    try:
        names = sorted({row.domain for row in rows})
        domains = db.query(DomainKeywords.id, DomainKeywords.domain_name).filter(DomainKeywords.domain_name.in_(names)).all()
        ids: dict[str, list[int]] = {}
        for domain in domains:
            ids.setdefault(domain.domain_name, []).append(domain.id)
        errors = [f"domain {name!r} not found" for name in names if name not in ids]
        errors += [f"domain {name!r} is ambiguous (ids {found})" for name, found in ids.items() if len(found) > 1]
        if errors:
            raise CsvImportError("\n".join(errors))
        domain_ids = {name: found[0] for name, found in ids.items()}

        existing = {}
        if inspect(db.get_bind()).has_table(TermCorrection.__tablename__):  # --dry-run은 테이블을 만들지 않는다
            existing = {
                (row.domain_id, row.wrong): row.correct
                for row in db.query(TermCorrection).filter(TermCorrection.domain_id.in_(domain_ids.values())).all()
            }
        summary = {"domains": len(domain_ids), "inserted": 0, "updated": 0, "unchanged": 0, "deleted": 0}
        incoming = {(domain_ids[row.domain], row.wrong): row.correct for row in rows}
        for key, correct in incoming.items():
            if key not in existing:
                summary["inserted"] += 1
            elif existing[key] == correct:
                summary["unchanged"] += 1
            else:
                summary["updated"] += 1
        if replace:
            summary["deleted"] = len(existing.keys() - incoming.keys())
        if dry_run:
            return summary

        if replace:
            db.query(TermCorrection).filter(TermCorrection.domain_id.in_(domain_ids.values())).delete(synchronize_session=False)
        statement = insert(TermCorrection).values(
            [{"domain_id": domain_id, "wrong": wrong, "correct": correct} for (domain_id, wrong), correct in incoming.items()]
        )
        db.execute(statement.on_conflict_do_update(
            constraint="uq_term_corrections_domain_wrong", set_={"correct": statement.excluded.correct}
        ))
        db.commit()
        return summary
    finally:
        db.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Import term corrections (wrong -> correct) from CSV")
    parser.add_argument("csv_path")
    parser.add_argument("--replace", action="store_true", help="delete existing entries of the domains in the file first")
    parser.add_argument("--dry-run", action="store_true", help="validate and report without writing")
    args = parser.parse_args(argv)
    try:
        with open(args.csv_path, encoding="utf-8-sig", newline="") as f:
            rows = parse_rows(f)
        if not rows:
            raise CsvImportError("no rows to import")
        if not args.dry_run:
            init_db()  # 새 버전 배포 전이라 테이블이 아직 없을 수 있다(앱 기동과 같은 create_all)
        summary = import_rows(rows, replace=args.replace, dry_run=args.dry_run)
    except CsvImportError as e:
        print(f"import failed, nothing written:\n{e}", file=sys.stderr)
        return 1
    mode = " (dry run, nothing written)" if args.dry_run else ""
    print(
        f"{summary['domains']} domain(s): {summary['inserted']} inserted, {summary['updated']} updated, "
        f"{summary['unchanged']} unchanged, {summary['deleted']} deleted{mode}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
