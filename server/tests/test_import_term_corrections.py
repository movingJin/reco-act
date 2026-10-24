"""용어 사전 CSV import 스크립트 (01-plan 3.4, PR-9). server/.env의 PostgreSQL 서버에 만든 테스트 전용 DB(conftest)에서 돈다."""
import pytest

from database import DomainKeywords, TermCorrection
from scripts import import_term_corrections as script


@pytest.fixture
def domains(db, monkeypatch):
    monkeypatch.setattr(script, "init_db", lambda: None)  # 테스트 DB에는 테이블이 이미 있다(앱 engine은 가짜 주소)
    with db() as session:
        search = DomainKeywords(domain_name="검색", keywords=["벡터DB", "엘라스틱서치"])
        infra = DomainKeywords(domain_name="인프라", keywords=["쿠버네티스"])
        session.add_all([search, infra])
        session.commit()
        return {"검색": search.id, "인프라": infra.id}


def entries(db):
    with db() as session:
        return sorted((row.domain_id, row.wrong, row.correct) for row in session.query(TermCorrection).all())


def write_csv(tmp_path, text, bom=False):
    path = tmp_path / "terms.csv"
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return str(path)


def test_import_inserts_and_updates(db, domains, tmp_path, capsys):
    first = write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터DB\n검색,엘라스틱 서치,엘라스틱서치\n인프라,쿠버 네티스,쿠버네티스\n", bom=True)
    assert script.main([first]) == 0
    assert "2 domain(s): 3 inserted, 0 updated, 0 unchanged, 0 deleted" in capsys.readouterr().out

    second = write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터 DB\n검색,엘라스틱 서치,엘라스틱서치\n\n")
    assert script.main([second]) == 0
    assert "1 domain(s): 0 inserted, 1 updated, 1 unchanged, 0 deleted" in capsys.readouterr().out

    assert entries(db) == sorted([
        (domains["검색"], "벡타디비", "벡터 DB"),
        (domains["검색"], "엘라스틱 서치", "엘라스틱서치"),
        (domains["인프라"], "쿠버 네티스", "쿠버네티스"),
    ])


def test_replace_drops_entries_missing_from_file(db, domains, tmp_path, capsys):
    script.main([write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터DB\n검색,엘라스틱 서치,엘라스틱서치\n인프라,쿠버 네티스,쿠버네티스\n")])

    assert script.main([write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터DB\n"), "--replace"]) == 0

    assert "1 deleted" in capsys.readouterr().out
    # 파일에 나온 도메인(검색)만 비우고 채운다. 인프라는 그대로다
    assert entries(db) == sorted([(domains["검색"], "벡타디비", "벡터DB"), (domains["인프라"], "쿠버 네티스", "쿠버네티스")])


def test_dry_run_writes_nothing(db, domains, tmp_path, capsys):
    assert script.main([write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터DB\n"), "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "1 inserted" in out and "dry run, nothing written" in out
    assert entries(db) == []


@pytest.mark.parametrize("text, message", [
    ("domain,wrong\n검색,벡타디비\n", "header must be exactly"),
    ("domain,wrong,correct\n검색,,벡터DB\n", "line 2: domain, wrong, correct must all be filled"),
    ("domain,wrong,correct\n검색,벡터DB,벡터DB\n", "line 2: wrong and correct are the same"),
    ("domain,wrong,correct\n검색,벡타디비,벡터DB\n검색,벡타디비,벡터 DB\n", "line 3: '벡타디비' in '검색' already maps to '벡터DB' on line 2"),
    ("domain,wrong,correct\n없는도메인,벡타디비,벡터DB\n", "domain '없는도메인' not found"),
    ("domain,wrong,correct\n", "no rows to import"),
])
def test_invalid_file_writes_nothing(db, domains, tmp_path, capsys, text, message):
    # 앞줄이 정상이어도 한 줄이라도 틀리면 아무것도 쓰지 않는다
    assert script.main([write_csv(tmp_path, text)]) == 1

    assert message in capsys.readouterr().err
    assert entries(db) == []


def test_ambiguous_domain_name_is_rejected(db, domains, tmp_path, capsys):
    with db() as session:
        session.add(DomainKeywords(domain_name="검색", keywords=[]))
        session.commit()

    assert script.main([write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터DB\n")]) == 1
    assert "domain '검색' is ambiguous" in capsys.readouterr().err


def test_deleting_domain_deletes_its_entries(db, domains, tmp_path):
    from services.domain_service import delete_domain

    script.main([write_csv(tmp_path, "domain,wrong,correct\n검색,벡타디비,벡터DB\n인프라,쿠버 네티스,쿠버네티스\n")])
    assert delete_domain(domains["검색"])

    assert entries(db) == [(domains["인프라"], "쿠버 네티스", "쿠버네티스")]
