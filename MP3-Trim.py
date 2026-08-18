import os
import sys
import csv
import time
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


def get_resource_path(relative_path):
    """ Βρίσκει τη διαδρομή είτε τρέχουμε σκριπτάκι είτε τελικό .exe """
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# Προκαθορισμένα επίπεδα ευαισθησίας για το κόψιμο σιωπής (threshold σε dB)
SENSITIVITY_LEVELS = {
    "Ήπιο (κόβει λιγότερο)": "-50dB",
    "Κανονικό": "-45dB",
    "Επιθετικό (κόβει περισσότερο)": "-35dB",
}


class MP3ProcessorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Audio Harmonizer Pro")
        self.root.geometry("520x560")
        self.root.config(bg="#1e1e1e")
        self.root.resizable(False, False)

        try:
            self.root.iconbitmap(get_resource_path("icon.ico"))
        except Exception:
            pass

        self.input_dir = ""
        self.output_dir = ""
        self.cancel_requested = False
        self.processing = False

        title_label = tk.Label(
            root,
            text="🎵 Batch MP3 Harmonizer & Trimmer",
            fg="#00ffcc",
            bg="#1e1e1e",
            font=("Arial", 14, "bold")
        )
        title_label.pack(pady=12)

        # 1. Κουμπί & Label Εισόδου
        self.btn_in = tk.Button(
            root, text="📥 1. Επιλογή Φακέλου με τα MP3",
            command=self.select_input,
            bg="#333333", fg="white", font=("Arial", 10, "bold"),
            width=42, pady=6, relief="flat", cursor="hand2"
        )
        self.btn_in.pack(pady=3)

        self.lbl_in = tk.Label(root, text="Δεν έχει επιλεχθεί φάκελος", fg="#888888", bg="#1e1e1e", font=("Arial", 9))
        self.lbl_in.pack(pady=2)

        # 2. Κουμπί & Label Εξόδου
        self.btn_out = tk.Button(
            root, text="📤 2. Επιλογή Φακέλου Προορισμού",
            command=self.select_output,
            bg="#333333", fg="white", font=("Arial", 10, "bold"),
            width=42, pady=6, relief="flat", cursor="hand2"
        )
        self.btn_out.pack(pady=6)

        self.lbl_out = tk.Label(root, text="Δεν έχει επιλεχθεί φάκελος", fg="#888888", bg="#1e1e1e", font=("Arial", 9))
        self.lbl_out.pack(pady=2)

        # 3. Ρυθμίσεις ευαισθησίας
        settings_frame = tk.Frame(root, bg="#1e1e1e")
        settings_frame.pack(pady=10)

        tk.Label(settings_frame, text="Ευαισθησία Κοψίματος Σιωπής:", fg="#cccccc", bg="#1e1e1e",
                 font=("Arial", 9)).grid(row=0, column=0, sticky="w", padx=5, pady=3)

        self.sensitivity_var = tk.StringVar(value="Κανονικό")
        sensitivity_combo = ttk.Combobox(
            settings_frame, textvariable=self.sensitivity_var,
            values=list(SENSITIVITY_LEVELS.keys()), state="readonly", width=28
        )
        sensitivity_combo.grid(row=0, column=1, padx=5, pady=3)

        # 4. Checkbox: παράλειψη ήδη επεξεργασμένων
        self.skip_existing_var = tk.BooleanVar(value=True)
        skip_check = tk.Checkbutton(
            root, text="Παράλειψη αρχείων που υπάρχουν ήδη στον προορισμό",
            variable=self.skip_existing_var,
            fg="#cccccc", bg="#1e1e1e", selectcolor="#333333",
            activebackground="#1e1e1e", activeforeground="#cccccc", font=("Arial", 9)
        )
        skip_check.pack(pady=3)

        # 5. Preview & Run κουμπιά
        btns_frame = tk.Frame(root, bg="#1e1e1e")
        btns_frame.pack(pady=10)

        self.btn_preview = tk.Button(
            btns_frame, text="🎧 Preview σε 1 Αρχείο",
            command=self.run_preview,
            bg="#555555", fg="white", font=("Arial", 10, "bold"),
            width=20, pady=6, relief="flat", cursor="hand2"
        )
        self.btn_preview.grid(row=0, column=0, padx=5)

        self.btn_run = tk.Button(
            btns_frame, text="🚀 Εκκίνηση Batch",
            command=self.start_thread,
            bg="#444444", fg="#777777", font=("Arial", 10, "bold"),
            width=20, pady=6, relief="flat", cursor="arrow", state="disabled"
        )
        self.btn_run.grid(row=0, column=1, padx=5)

        self.btn_cancel = tk.Button(
            root, text="✖ Ακύρωση", command=self.request_cancel,
            bg="#661111", fg="white", font=("Arial", 9, "bold"),
            width=15, pady=4, relief="flat", cursor="hand2", state="disabled"
        )
        self.btn_cancel.pack(pady=4)

        # Status Label & Progress Bar
        self.lbl_status = tk.Label(root, text="Έτοιμο για δράση...", fg="#aaaaaa", bg="#1e1e1e", font=("Arial", 9, "italic"))
        self.lbl_status.pack(pady=6)

        self.progress = ttk.Progressbar(root, orient="horizontal", length=440, mode="determinate")
        self.progress.pack(pady=4)

        self.lbl_eta = tk.Label(root, text="", fg="#888888", bg="#1e1e1e", font=("Arial", 8))
        self.lbl_eta.pack(pady=2)

        # Log box
        self.log_box = tk.Text(root, height=8, width=62, bg="#111111", fg="#00ff88",
                                font=("Consolas", 8), relief="flat")
        self.log_box.pack(pady=8)
        self.log_box.config(state="disabled")

    # ---------- UI helpers (thread-safe) ----------

    def ui(self, fn, *args, **kwargs):
        """ Ασφαλής τρόπος να καλείς UI updates από άλλο thread """
        self.root.after(0, lambda: fn(*args, **kwargs))

    def log(self, message):
        def _write():
            self.log_box.config(state="normal")
            self.log_box.insert(tk.END, message + "\n")
            self.log_box.see(tk.END)
            self.log_box.config(state="disabled")
        self.ui(_write)

    # ---------- Selection ----------

    def select_input(self):
        folder = filedialog.askdirectory(title="Επίλεξε τον φάκελο με τα raw MP3")
        if folder:
            self.input_dir = folder
            display_name = os.path.basename(folder) or folder
            self.lbl_in.config(text=f"📥 {display_name}", fg="#00ffcc")
            self.check_ready()

    def select_output(self):
        folder = filedialog.askdirectory(title="Επίλεξε φάκελο προορισμού για τα έτοιμα αρχεία")
        if folder:
            self.output_dir = folder
            display_name = os.path.basename(folder) or folder
            self.lbl_out.config(text=f"📤 {display_name}", fg="#00ffcc")
            self.check_ready()

    def check_ready(self):
        if self.input_dir and self.output_dir:
            self.btn_run.config(state="normal", bg="#00aa55", fg="white", cursor="hand2")

    # ---------- ffmpeg helpers ----------

    def get_ffmpeg_path(self):
        return get_resource_path(os.path.join("bin", "ffmpeg.exe"))

    def get_ffprobe_path(self):
        return get_resource_path(os.path.join("bin", "ffprobe.exe"))

    def check_ffmpeg_available(self):
        ffmpeg_path = self.get_ffmpeg_path()
        if not os.path.exists(ffmpeg_path):
            messagebox.showerror(
                "Σφάλμα",
                f"Δεν βρέθηκε το ffmpeg.exe στη διαδρομή:\n{ffmpeg_path}\n\n"
                "Βεβαιώσου ότι ο φάκελος 'bin' με το ffmpeg.exe βρίσκεται δίπλα στο πρόγραμμα."
            )
            return False
        return True

    def get_duration(self, filepath):
        """ Επιστρέφει τη διάρκεια σε δευτερόλεπτα, ή None αν αποτύχει """
        ffprobe_path = self.get_ffprobe_path()
        if not os.path.exists(ffprobe_path):
            return None
        try:
            creationflags = 0x08000000 if os.name == 'nt' else 0
            result = subprocess.run(
                [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags
            )
            return round(float(result.stdout.decode().strip()), 2)
        except Exception:
            return None

    def build_audio_filters(self):
        threshold = SENSITIVITY_LEVELS[self.sensitivity_var.get()]
        return (
            f"silenceremove=start_periods=1:start_duration=0.5:start_threshold={threshold},"
            "areverse,"
            f"silenceremove=start_periods=1:start_duration=0.5:start_threshold={threshold},"
            "areverse,"
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        )

    def process_one_file(self, in_file, out_file):
        ffmpeg_path = self.get_ffmpeg_path()
        cmd = [
            ffmpeg_path, "-y", "-i", in_file,
            "-af", self.build_audio_filters(),
            "-b:a", "320k",
            out_file
        ]
        creationflags = 0x08000000 if os.name == 'nt' else 0
        result = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags
        )
        return result

    # ---------- Preview ----------

    def run_preview(self):
        if self.processing:
            return
        if not self.check_ffmpeg_available():
            return
        filepath = filedialog.askopenfilename(
            title="Επίλεξε ένα MP3 για preview",
            filetypes=[("MP3 files", "*.mp3")]
        )
        if not filepath:
            return

        self.btn_preview.config(state="disabled", bg="#333333")
        self.lbl_status.config(text="Preview σε εξέλιξη...")
        threading.Thread(target=self._preview_worker, args=(filepath,), daemon=True).start()

    def _preview_worker(self, filepath):
        folder = os.path.dirname(filepath)
        name, ext = os.path.splitext(os.path.basename(filepath))
        preview_out = os.path.join(folder, f"{name}_preview{ext}")

        orig_duration = self.get_duration(filepath)
        result = self.process_one_file(filepath, preview_out)
        new_duration = self.get_duration(preview_out) if result.returncode == 0 else None

        def _done():
            self.btn_preview.config(state="normal", bg="#555555")
            self.lbl_status.config(text="Έτοιμο για δράση...")
            if result.returncode == 0:
                msg = f"Το preview αποθηκεύτηκε:\n{preview_out}"
                if orig_duration and new_duration:
                    msg += f"\n\nΔιάρκεια πριν: {orig_duration}s\nΔιάρκεια μετά: {new_duration}s"
                messagebox.showinfo("Preview Έτοιμο", msg)
            else:
                err = result.stderr.decode(errors="ignore")[-500:]
                messagebox.showerror("Σφάλμα Preview", f"Απέτυχε η επεξεργασία:\n\n{err}")
        self.ui(_done)

    # ---------- Batch ----------

    def request_cancel(self):
        self.cancel_requested = True
        self.lbl_status.config(text="Ακύρωση... ολοκληρώνεται το τρέχον αρχείο")

    def start_thread(self):
        if not self.check_ffmpeg_available():
            return
        self.processing = True
        self.cancel_requested = False
        self.btn_in.config(state="disabled")
        self.btn_out.config(state="disabled")
        self.btn_preview.config(state="disabled", bg="#333333")
        self.btn_run.config(state="disabled", bg="#444444")
        self.btn_cancel.config(state="normal")
        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")
        threading.Thread(target=self.run_process, daemon=True).start()

    def run_process(self):
        all_files = [f for f in os.listdir(self.input_dir) if f.lower().endswith(".mp3")]
        if not all_files:
            self.ui(messagebox.showwarning, "Προσοχή", "Δεν βρέθηκαν MP3 αρχεία στον φάκελο εισόδου!")
            self.reset_ui()
            return

        os.makedirs(self.output_dir, exist_ok=True)

        skip_existing = self.skip_existing_var.get()
        files_to_process = []
        skipped = []
        for f in all_files:
            out_path = os.path.join(self.output_dir, f)
            if skip_existing and os.path.exists(out_path):
                skipped.append(f)
            else:
                files_to_process.append(f)

        if skipped:
            self.log(f"⏭ Παράλειψη {len(skipped)} ήδη υπαρχόντων αρχείων.")

        total_files = len(files_to_process)
        self.ui(self.progress.config, maximum=max(total_files, 1), value=0)

        log_rows = []
        success_count = 0
        file_times = []

        for i, filename in enumerate(files_to_process):
            if self.cancel_requested:
                self.log("⛔ Η επεξεργασία ακυρώθηκε από τον χρήστη.")
                break

            self.ui(self.lbl_status.config, text=f"Επεξεργασία ({i+1}/{total_files}): {filename}")
            start_time = time.time()

            in_file = os.path.join(self.input_dir, filename)
            out_file = os.path.join(self.output_dir, filename)

            orig_duration = self.get_duration(in_file)
            result = self.process_one_file(in_file, out_file)
            elapsed = time.time() - start_time
            file_times.append(elapsed)

            if result.returncode == 0:
                success_count += 1
                new_duration = self.get_duration(out_file)
                status = "OK"
                error_msg = ""
                self.log(f"✅ {filename} ({elapsed:.1f}s)")
            else:
                new_duration = None
                status = "ΣΦΑΛΜΑ"
                error_msg = result.stderr.decode(errors="ignore")[-300:].replace("\n", " ")
                self.log(f"❌ {filename} — {error_msg[:120]}")

            log_rows.append({
                "filename": filename,
                "status": status,
                "original_duration_sec": orig_duration if orig_duration else "",
                "new_duration_sec": new_duration if new_duration else "",
                "error": error_msg
            })

            self.ui(self.progress.config, value=i + 1)

            # Εκτίμηση χρόνου που απομένει
            avg_time = sum(file_times) / len(file_times)
            remaining = total_files - (i + 1)
            eta_sec = int(avg_time * remaining)
            eta_text = f"Εκτιμώμενος χρόνος που απομένει: ~{eta_sec}s" if remaining > 0 else ""
            self.ui(self.lbl_eta.config, text=eta_text)

        # Εγγραφή log CSV
        log_path = os.path.join(self.output_dir, "processing_log.csv")
        try:
            with open(log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "filename", "status", "original_duration_sec", "new_duration_sec", "error"
                ])
                writer.writeheader()
                writer.writerows(log_rows)
            self.log(f"📄 Το log αποθηκεύτηκε στο: {log_path}")
        except Exception as e:
            self.log(f"⚠ Αποτυχία αποθήκευσης log: {e}")

        processed = len(log_rows)
        self.ui(self.lbl_status.config, text="Ολοκληρώθηκε!" if not self.cancel_requested else "Ακυρώθηκε.")

        summary = (
            f"Αλέστηκαν επιτυχώς: {success_count}/{processed} κομμάτια.\n"
            f"Παραλείφθηκαν (ήδη υπήρχαν): {len(skipped)}\n"
            f"Αποθηκεύτηκαν στον φάκελο:\n{self.output_dir}\n\n"
            f"Λεπτομερές log: processing_log.csv"
        )
        if self.cancel_requested:
            summary = "Η διαδικασία ακυρώθηκε.\n\n" + summary

        self.ui(messagebox.showinfo, "Ολοκληρώθηκε" if not self.cancel_requested else "Ακυρώθηκε", summary)
        self.reset_ui()

    def reset_ui(self):
        self.processing = False
        self.cancel_requested = False

        def _reset():
            self.btn_in.config(state="normal")
            self.btn_out.config(state="normal")
            self.btn_preview.config(state="normal", bg="#555555")
            self.btn_run.config(state="normal", bg="#00aa55")
            self.btn_cancel.config(state="disabled")
            self.progress["value"] = 0
            self.lbl_eta.config(text="")
            self.lbl_status.config(text="Έτοιμο για δράση...")
        self.ui(_reset)


if __name__ == "__main__":
    root = tk.Tk()
    app = MP3ProcessorApp(root)
    root.mainloop()
