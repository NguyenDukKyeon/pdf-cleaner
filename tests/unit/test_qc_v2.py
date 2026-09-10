from pathlib import Path
import fitz
from backend.engine.pipeline_v2.report import ProcessingReport
from backend.engine.qc_v2.validator import validate_output


def _pdf(path: Path, *, pages=2, width=300, height=400, altered=False):
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=width, height=height)
        p.insert_text((40, 60), f'BASE PAGE {i}')
        if altered:
            p.draw_rect(fitz.Rect(0, 0, width * .7, height * .7), color=(0,0,0), fill=(0,0,0))
    doc.save(path); doc.close()


def _report(path: Path, regions=None):
    return ProcessingReport('stream_remove', .96, str(path), metadata={'watermark_regions': regions or []})


def test_qc_rejects_page_count_and_geometry_loss(tmp_path):
    src=tmp_path/'src.pdf'; _pdf(src,pages=2)
    fewer=tmp_path/'fewer.pdf'; _pdf(fewer,pages=1)
    q1=validate_output(src,fewer,_report(fewer)); assert not q1.ok and not q1.page_count_ok
    wrong=tmp_path/'wrong.pdf'; _pdf(wrong,pages=2,width=350)
    q2=validate_output(src,wrong,_report(wrong)); assert not q2.ok and not q2.geometry_ok


def test_qc_rejects_excessive_changes_outside_watermark_regions(tmp_path):
    src=tmp_path/'src.pdf'; out=tmp_path/'out.pdf'; _pdf(src); _pdf(out,altered=True)
    qc=validate_output(src,out,_report(out,[(0.9,0.9,1.0,1.0)]),max_outside_change_ratio=0.03)
    assert not qc.ok and qc.outside_change_ratio>0.03
