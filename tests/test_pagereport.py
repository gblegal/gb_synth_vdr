from pathlib import Path

from synthvdr.__main__ import main
from synthvdr.domain import DEFAULT_DOMAIN_ROOT, load_domain
from synthvdr.lengths import load_lengths
from synthvdr.pagereport import page_report, pdf_page_count, scan_estimate

LENGTHS = load_lengths(DEFAULT_DOMAIN_ROOT, load_domain(DEFAULT_DOMAIN_ROOT))
CUSTOMER = Path("05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01")
MINUTES = Path("01_corporate/1.3_board-minutes/1.3.1_board-minutes-01")

CONF = '''ROOM_CODENAME="Project Testbed"
INDEX_TOTAL=2
BLIND_TOTAL=2
FLAGGED_TOTAL=2
BLIND_TREE="data-room"
FLAGGED_TREE="_key/flagged"
KEY_ROOT="_key"
FLAG_STRING_1="Key diligence points"
FLAG_STRING_2="DD flag"
FINDING_PREFIXES="CORP|COMM"
EXPECTED_KDP_CARRIERS=0
SECTION_DIRS="01_corporate 05_commercial"
'''


def fake_pdf(path: Path, pages: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = b"<</Type /Pages /Count %d>>" % pages + b"<</Type /Page>>" * pages
    path.write_bytes(b"%PDF-1.4\n" + body)


def room(tmp_path, long=True):
    (tmp_path / "room.conf").write_text(CONF + ('DOC_LENGTH="long"\n' if long else ""))
    for stem, words in ((CUSTOMER, 11000), (MINUTES, 900)):
        md = tmp_path / "data-room" / (stem.as_posix() + ".md")
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text("word " * words)
    fake_pdf(tmp_path / "data-room-pdf" / (CUSTOMER.as_posix() + ".pdf"), 18)
    fake_pdf(tmp_path / "data-room-pdf" / (MINUTES.as_posix() + ".pdf"), 2)
    return tmp_path


def test_the_fixture_writes_the_full_dotted_filenames(tmp_path):
    r = room(tmp_path)
    assert (r / "data-room" / "05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01.md").is_file()
    assert (r / "data-room-pdf" / "05_commercial/5.1_customer-contracts/5.1.1_customer-contracts-01.pdf").is_file()
    assert (r / "data-room" / "01_corporate/1.3_board-minutes/1.3.1_board-minutes-01.md").is_file()
    assert (r / "data-room-pdf" / "01_corporate/1.3_board-minutes/1.3.1_board-minutes-01.pdf").is_file()


def test_pdf_page_count_counts_page_objects_not_the_pages_tree(tmp_path):
    fake_pdf(tmp_path / "a.pdf", 3)
    assert pdf_page_count(tmp_path / "a.pdf") == 3


def test_page_report_reports_agreements_by_band_and_counts_those_below(tmp_path):
    r = room(tmp_path)
    lines = page_report(r / "data-room", r / "data-room-pdf", LENGTHS)
    principal = next(line for line in lines if line.startswith("principal"))
    assert "n=  1" in principal and "min 18" in principal and "1 below" in principal
    assert any(line.startswith("heavy") and "no rendered agreements" in line for line in lines)


def test_scan_estimate_uses_words_per_page_and_the_measured_per_page_sizes(tmp_path):
    r = room(tmp_path)
    csv_path = r / "_key" / "scanned.csv"
    csv_path.parent.mkdir()
    csv_path.write_text("slot\n" + CUSTOMER.as_posix() + "\n")
    estimate = scan_estimate(r / "data-room", csv_path)
    assert "1 scanned document(s), ~22 page(s)" in estimate
    assert "~50MB" in estimate


def test_cli_says_there_is_nothing_to_report_in_a_short_room(tmp_path, capsys):
    assert main(["pages", "--room", str(room(tmp_path, long=False))]) == 0
    assert "DOC_LENGTH is short" in capsys.readouterr().out


def test_cli_reports_bands_in_a_long_room(tmp_path, capsys):
    assert main(["pages", "--room", str(room(tmp_path))]) == 0
    assert "principal" in capsys.readouterr().out


def test_scan_estimate_names_listed_slots_it_cannot_find(tmp_path):
    r = room(tmp_path)
    csv_path = r / "_key" / "scanned.csv"
    csv_path.parent.mkdir()
    csv_path.write_text("slot\n" + CUSTOMER.as_posix() + "\n" + "05_commercial/5.1_customer-contracts/5.1.9_customer-contracts-09\n")
    estimate = scan_estimate(r / "data-room", csv_path)
    assert "2 scanned document(s), ~22 page(s)" in estimate
    assert "1 listed slot(s) have no source" in estimate


def test_cli_estimate_refuses_without_a_scanned_csv(tmp_path, capsys):
    assert main(["pages", "--room", str(room(tmp_path)), "--estimate"]) == 2
    assert "scanned.csv" in capsys.readouterr().err
