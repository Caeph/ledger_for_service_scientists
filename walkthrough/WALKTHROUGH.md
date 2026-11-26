# Walkthrough: a liver diet study, from first analysis to corrected figure

About 15 minutes. The sample files are in `walkthrough/sample_files/`. Start the app with `.venv/bin/python run.py --demo` and open http://localhost:8000 (or whichever port it is running on). Success and error messages pop up in the bottom-right corner.

**The story:** you get liver RNA expression data for mice on a high-fat diet (HFD) or a control diet. You compute fold changes, make a figure and a report, and finalize. Then you find a data-entry error in the input (Fasn, sample `hfd_2`, is negative). You redo the analysis as a new iteration without losing the original.

---

## Part A — First analysis (log in as `jana` / `analyst123`)

**1. Dashboard.** You see the three demo projects, each with open and finalized session counts, last activity and the latest output. Below them is the recent-activity feed.

**2. Create a project.** Click **+ New project**.
- Name: `Liver diet study`
- Description: `HFD vs control, 12 weeks`
- Tags: `mouse, liver`

**3. Create a session.** On the project page, click **New session** (the project is pre-ticked).
- Title: `Fold change HFD vs control`
- Notes: `First pass, data from the core facility.`
- Tags: `fold-change`

The session page has three columns: INPUT · METHOD · OUTPUT. The status badge says **open**.

**4. Add inputs.** In the INPUT column, open **Upload input file(s)**. Select **both** `round1/liver_expression.tsv` and `round1/sample_sheet.csv` (Ctrl-click). Note: `from core facility`. Tags: `raw`. Click **Upload**.
- Click **preview** on `liver_expression.tsv`: it shows as a table. Look at the `Fasn` row. The value in `hfd_2` is **-12**, which is impossible for a count. That's our bug, but we don't notice it yet.
- A red box above the columns lists what's still missing before you can finalize.

**5. Record the method.** In METHOD, open **Record method**.
- Repository name: `liver-analysis`
- Commit SHA: `9f8e7d6c5b4a39281706f5e4d3c2b1a098765432`
- Branch: `main`
- Script paths: `fold_change.py`
- Code snapshot: `round1/fold_change.py`

It shows **not verified**, because no git repository is mounted on the server, so the commit can't be checked. Try entering `abc123` as the SHA: it's rejected, because a full 40-character SHA is required.

**6. Try to finalize too early.** The red box still says *at least one OUTPUT version*. Finalize is blocked until all three parts are present.

**7. Add outputs.** In OUTPUT, upload `round1/fold_change.svg` and `round1/report.html`. Note: `Figure 3 draft`. Preview both. The HTML report opens in a sandboxed frame, so any scripts in it can't touch the app.

**8. Finalize.** Click **Finalize session…**. A confirmation dialog lists what will be frozen. Click **Yes, finalize**. The badge turns **finalized**, and the upload and method forms are gone. You can still edit notes, annotations and tags.

## Part B — Fix the error without losing history

**9. Start an iteration.** Click **Start new session from this one**. You get a new **open** session, *"… (iteration)"*, pre-filled with the same two inputs and the same method. It says *Iteration of …* with a link back.

**10. Upload the corrected input.** Upload `round2/liver_expression.tsv`. As soon as you pick the file, a box warns that the name **already exists** and offers *new version* (default) or *new file*. Keep **new version**. Note: `fixed Fasn hfd_2`.
- The column now shows `liver_expression.tsv` **v2**. `sample_sheet.csv` stays at v1, because it didn't change.

**11. Upload the new outputs.** Upload `round2/fold_change.svg` and `round2/report.html`. You get the same warning, so keep *new version*. They become **v2** of the figure and the report.

**12. Finalize** the iteration the same way as in step 8.

**13. Look at the old session.** Click the *Iteration of* link back to the first session. Its files are untouched (v1), but each one now has a red **newer: v2** badge.

## Part C — Answer the three questions the tool exists for

**14. "Which input produced this figure?"** Click `fold_change.svg`. The artifact page lists v2 and v1. Each has its SHA-256 hash and a **Produced by session** block naming the exact input versions and the commit SHA. v1 came from the bad `liver_expression.tsv` v1, and v2 from the fixed v2. Click **preview** on each figure to compare them.

**15. "Where was this input used?"** Click `liver_expression.tsv`. Each version lists **Used in sessions**.

**16. Annotate after finalize.** On the figure's v2, open **Edit annotation & tags**.
- Text: `Figure 3 in manuscript`
- Key=value: `journal=Hepatology` (one per line)
- Tags: `figure-3`

Saving works even though the session is final.

**17. Download the session.** On the iteration session, click **Download zip**. It contains `inputs/`, `outputs/`, `method/` and `manifest.json`, with hashes, versions, the commit and annotations.

**18. Search.** Try `figure-3` (tag), `liver_expression` (file name), `core facility` (note) and `9f8e7d` (commit SHA).

**19. Dashboard: "What's the latest final output?"** The *Liver diet study* card shows **2 finalized**, and the latest output is the corrected figure, v2.

## Part D — Admin (log out, log in as `admin` / `admin12345`)

**20. Admin page.**
- *Audit log:* every step you just did, with user and UTC time.
- *Users:* create a user, deactivate one, or reset a password.

**21. Archive instead of delete.** Open the *Proteomics pilot* project and click **Archive project**. It disappears from the dashboard. Tick **show archived** to see it again, or unarchive it from the Admin page. Nothing can be deleted, by anyone.
