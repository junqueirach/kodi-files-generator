import csv
import os
import re
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from tkinter.scrolledtext import ScrolledText
from xml.sax.saxutils import escape

# -----------------------
# Robust text loading
# -----------------------
def read_text_auto(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, "rb") as f:
        return f.read().decode("utf-8", errors="ignore"), "utf-8(ignore)"

def open_csv_auto(path):
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            f = open(path, "r", encoding=enc, newline="")
            f.read(1024)
            f.seek(0)
            return f, enc
        except UnicodeDecodeError:
            continue
    return open(path, "r", encoding="utf-8", errors="ignore", newline=""), "utf-8(ignore)"

# -----------------------
# Template-safe replacements
# -----------------------
def replace_simple_tag(doc, tag, value):
    if value is None:
        value = ""
    value = escape(str(value))
    pattern = re.compile(rf"(<{tag}>)(.*?)(</{tag}>)", re.DOTALL)
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), doc, count=1)

def replace_attr_tag(doc, tag, attr_regex, value):
    if value is None:
        value = ""
    value = escape(str(value))
    pattern = re.compile(rf"(<{tag}\s+{attr_regex}>)(.*?)(</{tag}>)", re.DOTALL)
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), doc, count=1)

def sanitize_filename(text):
    return re.sub(r'[\\/:*?"<>|]', "-", text)

# -----------------------
# Core generation
# -----------------------
def generate_files(csv_path, nfo_template_path, xml_template_path, out_dir, show_name, log, progress, btn):
    def write_log(msg):
        log.configure(state="normal")
        log.insert("end", msg + "\n")
        log.see("end")
        log.configure(state="disabled")

    try:
        os.makedirs(out_dir, exist_ok=True)

        nfo_template, _ = read_text_auto(nfo_template_path)
        xml_template, _ = read_text_auto(xml_template_path)

        f, _ = open_csv_auto(csv_path)

        with f:
            reader = csv.DictReader(f)
            rows = list(reader)

        progress["maximum"] = len(rows)
        progress["value"] = 0

        for i, row in enumerate(rows, start=1):
            season = int(row["Season"])
            episode = int(row["Episode"])
            title = row["Title"] or ""
            safe_title = sanitize_filename(title)

            base_name = f"{show_name} - S{season:02d}E{episode:02d} - {safe_title}"

            runtime_min = (row["Runtime_min"] or "").strip()
            runtime_sec = str(int(runtime_min) * 60) if runtime_min.isdigit() else ""

            # ---- NFO ----
            nfo = nfo_template
            nfo = replace_attr_tag(nfo, "id", r'type="tvdb"', row["TVDB_Episode_ID"])
            nfo = replace_attr_tag(nfo, "uniqueid", r'type="tvdb"\s+default="true"', row["TVDB_Episode_ID"])
            nfo = replace_attr_tag(nfo, "uniqueid", r'type="imdb"\s+default="false"', row["IMDB_ID"])

            nfo = replace_simple_tag(nfo, "title", row["Title"])
            nfo = replace_simple_tag(nfo, "season", row["Season"])
            nfo = replace_simple_tag(nfo, "episode", row["Episode"])
            nfo = replace_simple_tag(nfo, "aired", row["Originally_Aired"])
            nfo = replace_simple_tag(nfo, "runtime", runtime_min)
            nfo = replace_simple_tag(nfo, "durationinseconds", runtime_sec)
            nfo = replace_simple_tag(nfo, "mpaa", row["Content_Rating"])
            nfo = replace_simple_tag(nfo, "plot", row["Description"])

            # ---- XML ----
            xml = xml_template
            xml = replace_simple_tag(xml, "ID", row["ID"])
            xml = replace_simple_tag(xml, "EpisodeID", row["TVDB_Episode_ID"])   # ✅ NEW
            xml = replace_simple_tag(xml, "SeasonNumber", row["Season"])
            xml = replace_simple_tag(xml, "EpisodeNumber", row["Episode"])
            xml = replace_simple_tag(xml, "EpisodeName", row["Title"])
            xml = replace_simple_tag(xml, "FirstAired", row["Originally_Aired"])
            xml = replace_simple_tag(xml, "IMDB_ID", row["IMDB_ID"])
            xml = replace_simple_tag(xml, "Overview", row["Description"])
            xml = replace_simple_tag(xml, "VideoLength", runtime_min)
            xml = replace_simple_tag(xml, "VideoLengthSeconds", runtime_sec)
            xml = replace_simple_tag(xml, "Duration", runtime_min)
            xml = replace_simple_tag(xml, "DurationSeconds", runtime_sec)

            with open(os.path.join(out_dir, base_name + ".nfo"), "w", encoding="utf-8") as f_out:
                f_out.write(nfo)

            with open(os.path.join(out_dir, base_name + ".xml"), "w", encoding="utf-8") as f_out:
                f_out.write(xml)

            progress["value"] = i
            write_log(f"Created: {base_name}.nfo / .xml")

        messagebox.showinfo("Success", "All files generated successfully.")
    except Exception as e:
        messagebox.showerror("Error", str(e))
        write_log("ERROR: " + str(e))
    finally:
        btn.configure(state="normal")

# -----------------------
# GUI
# -----------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Kodi NFO + XML Generator")
        self.geometry("860x620")

        self.csv_path = tk.StringVar()
        self.nfo_path = tk.StringVar()
        self.xml_path = tk.StringVar()
        self.out_dir = tk.StringVar(value=os.path.join(os.getcwd(), "output"))
        self.show_name = tk.StringVar(value="Turma da Mônica")

        frm = ttk.Frame(self, padding=12)
        frm.pack(fill="both", expand=True)

        def browse(var, kinds):
            p = filedialog.askopenfilename(filetypes=kinds)
            if p:
                var.set(p)

        def row(label, var, kinds):
            r = ttk.Frame(frm)
            r.pack(fill="x", pady=6)
            ttk.Label(r, text=label, width=18).pack(side="left")
            ttk.Entry(r, textvariable=var).pack(side="left", fill="x", expand=True, padx=6)
            ttk.Button(r, text="Browse…", command=lambda: browse(var, kinds)).pack(side="left")

        row("CSV file", self.csv_path, [("CSV files", "*.csv")])
        row("NFO template", self.nfo_path, [("NFO files", "*.nfo"), ("Text files", "*.txt")])
        row("XML template", self.xml_path, [("XML files", "*.xml")])

        r = ttk.Frame(frm)
        r.pack(fill="x", pady=6)
        ttk.Label(r, text="Output folder", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.out_dir).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(r, text="Browse…", command=lambda: self.out_dir.set(filedialog.askdirectory())).pack(side="left")

        r = ttk.Frame(frm)
        r.pack(fill="x", pady=6)
        ttk.Label(r, text="Show name", width=18).pack(side="left")
        ttk.Entry(r, textvariable=self.show_name).pack(side="left", fill="x", expand=True, padx=6)

        self.progress = ttk.Progressbar(frm)
        self.progress.pack(fill="x", pady=10)

        self.btn = ttk.Button(frm, text="Generate files", command=self.run)
        self.btn.pack()

        ttk.Label(frm, text="Log").pack(anchor="w", pady=(12, 4))
        self.log = ScrolledText(frm, height=18, state="disabled")
        self.log.pack(fill="both", expand=True)

    def run(self):
        self.btn.configure(state="disabled")
        self.progress["value"] = 0
        threading.Thread(
            target=generate_files,
            args=(
                self.csv_path.get(),
                self.nfo_path.get(),
                self.xml_path.get(),
                self.out_dir.get(),
                self.show_name.get(),
                self.log,
                self.progress,
                self.btn
            ),
            daemon=True
        ).start()

if __name__ == "__main__":
    App().mainloop()