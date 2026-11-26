"""Demo data so the prototype has something to click through."""


def _svg_bar(values, title):
    w, bh = 360, 22
    bars = "".join(
        f'<rect x="110" y="{30 + i * (bh + 6)}" width="{v * 2.2:.0f}" height="{bh}" fill="#2f5fd0"/>'
        f'<text x="100" y="{45 + i * (bh + 6)}" font-size="12" text-anchor="end" font-family="sans-serif">{k}</text>'
        f'<text x="{116 + v * 2.2:.0f}" y="{45 + i * (bh + 6)}" font-size="11" font-family="sans-serif">{v}</text>'
        for i, (k, v) in enumerate(values))
    hgt = 40 + len(values) * (bh + 6)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{hgt}" viewBox="0 0 {w} {hgt}">'
            f'<rect width="100%" height="100%" fill="#fff"/><text x="10" y="18" font-size="14" font-weight="bold" '
            f'font-family="sans-serif">{title}</text>{bars}</svg>').encode()


def load_demo(t, admin_id):
    ana = t.create_user(admin_id, "jana", "Jana Analyst", "analyst123")
    p1 = t.create_project(admin_id, "Tumour transcriptomics", "RNA-seq of the 2026 cohort; DE analysis and figures.",
                          "rna-seq, cohort-2026")
    p2 = t.create_project(ana, "Survival models", "Cox models on the clinical table.", "clinical, survival")
    t.create_project(ana, "Proteomics pilot", "Pilot MS run, nothing analysed yet.", "proteomics")

    clinical_v1 = (b"patient_id\tage\tsex\tstage\tevent\ttime_months\n"
                   b"P001\t61\tF\tII\t1\t14\nP002\t55\tM\tI\t0\t36\nP003\t70\tM\tIII\t1\t8\nP004\t48\tF\tI\t0\t40\n")
    clinical_v2 = clinical_v1 + b"P005\t66\tF\tII\t1\t19\nP006\t59\tM\tIII\t1\t11\n"

    s1 = t.create_session(ana, "Baseline Cox model", [p2], "First pass. Stage only, no covariates.", "cox")
    t.upload(ana, s1, "INPUT", "clinical_table.tsv", clinical_v1, "export from REDCap 2026-10-02", "redcap")
    t.set_method(ana, s1, "survival-analysis", "", "3f9c2a1b7e4d5c6a8b9f0e1d2c3b4a5968778695", "main",
                 "scripts/cox_baseline.R", "R 4.4, survival 3.7")
    t.upload(ana, s1, "OUTPUT", "hazard_ratios.svg", _svg_bar([("Stage I", 20), ("Stage II", 55), ("Stage III", 95)],
                                                              "Hazard ratio ×100 by stage"), "Figure 2 draft")
    report = (b"<!doctype html><html><body style='font-family:sans-serif;padding:20px'><h1>Cox baseline report</h1>"
              b"<p>Concordance 0.71. Stage III HR 2.6 (95% CI 1.4-4.9).</p></body></html>")
    t.upload(ana, s1, "OUTPUT", "cox_report.html", report, "knitted report")
    t.annotate(ana, "session", s1, text="Shared with PI on 2026-10-05.", kv="reviewer=PI\nstatus=draft figure",
               tags="cox, figure-2")
    t.finalize(ana, s1)

    s2 = t.start_from(ana, s1)
    t.upload(ana, s2, "INPUT", "clinical_table.tsv", clinical_v2, "re-export with 2 new patients")
    t.update_session_notes(ana, s2, "Adds the two late-enrolled patients. Will rerun once the method is updated.")

    s3 = t.create_session(admin_id, "DE: tumour vs normal", [p1], "DESeq2 default settings.", "deseq2")
    t.upload(admin_id, s3, "INPUT", "counts.csv",
             b"gene,T1,T2,N1,N2\nTP53,120,98,40,51\nMYC,300,280,90,77\nEGFR,55,61,52,49\n", "featureCounts output")
    t.upload(admin_id, s3, "INPUT", "clinical_table.tsv", clinical_v1, "same clinical table as survival project")
    t.set_method(admin_id, s3, "rnaseq-pipeline", "", "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", "dev",
                 "notebooks/de_analysis.ipynb", "", snapshot=("de_analysis.py", b"import pandas as pd\n# DE analysis\n"))
    t.upload(admin_id, s3, "OUTPUT", "volcano.svg",
             _svg_bar([("TP53", 60), ("MYC", 100), ("EGFR", 5)], "log2FC ×20"), "volcano placeholder")
    t.finalize(admin_id, s3)
