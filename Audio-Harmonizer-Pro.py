__version__ = "1.1.2"

import os
import sys
import csv
import time
import json
import socket
import tempfile
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import winsound
import array

# Numpy (προαιρετικό) - χρησιμοποιείται για πραγματική ανάλυση συχνοτήτων
# στο Equalizer (πραγματικός παλμός από το κομμάτι που παίζει, όχι fake).
# Αν δεν υπάρχει εγκατεστημένο, γίνεται αυτόματα fallback σε ανάλυση έντασης
# χωρίς ζώνες συχνότητας -- εξακολουθεί να είναι 100% πραγματικό, απλά
# λιγότερο λεπτομερές (βλ. build_envelope_from_pcm παρακάτω).
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

# Drag & Drop Support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

# Audio Player Support
try:
    import pygame
    pygame.mixer.init()
    HAS_PYGAME = True
except Exception:
    HAS_PYGAME = False

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _is_dir_writable(path):
    """Ελέγχει αν μπορούμε ΠΡΑΓΜΑΤΙΚΑ να γράψουμε σε έναν φάκελο (όχι μόνο αν υπάρχει)."""
    try:
        test_file = os.path.join(path, f".__write_test_{os.getpid()}.tmp")
        with open(test_file, "w") as f:
            f.write("x")
        os.remove(test_file)
        return True
    except Exception:
        return False


def get_config_path():
    """
    Επιστρέφει το path αποθήκευσης του config.json.

    Όταν η εφαρμογή είναι εγκατεστημένη μέσω Inno Setup, συνήθως καταλήγει σε
    προστατευμένο φάκελο (π.χ. Program Files) όπου ένας απλός χρήστης των
    Windows ΔΕΝ έχει δικαίωμα εγγραφής χωρίς δικαιώματα διαχειριστή -- γι'
    αυτό οι ρυθμίσεις δεν αποθηκεύονταν ποτέ. Σε αυτή την περίπτωση
    αποθηκεύουμε αυτόματα στον προσωπικό φάκελο ρυθμίσεων των Windows
    (%APPDATA%), όπως κάνει κάθε κανονική εγκατεστημένη εφαρμογή. Αν ο
    φάκελος της εφαρμογής είναι εγγράψιμος (π.χ. portable χρήση ή εκτέλεση
    από τον κώδικα), οι ρυθμίσεις συνεχίζουν να αποθηκεύονται εκεί, όπως πριν.
    """
    if _is_dir_writable(APP_DIR):
        return os.path.join(APP_DIR, "config.json")

    appdata = os.getenv("APPDATA") or os.path.expanduser("~")
    config_dir = os.path.join(appdata, "AudioHarmonizerPro")
    try:
        os.makedirs(config_dir, exist_ok=True)
    except Exception:
        pass
    return os.path.join(config_dir, "config.json")


CONFIG_FILE = get_config_path()

# Αύξησε αυτό όταν αλλάξει η ΔΟΜΗ του config.json (π.χ. μετονομασία/αναδιάταξη
# ενός key) και πρόσθεσε το ανάλογο βήμα μέσα στο migrate_config() παρακάτω.
# Η προσθήκη ενός απλού νέου key με default τιμή ΔΕΝ χρειάζεται αύξηση --
# αυτό καλύπτεται ήδη αυτόματα από το merge στο load_config().
CONFIG_VERSION = 1

DEFAULT_CONFIG = {
    "config_version": CONFIG_VERSION,
    "language": "el",
    "export_format": "MP3",
    "export_bitrate": "320k",
    "export_samplerate": "Original",
    "sensitivity": "sensitivity_normal",
    "skip_existing": True
}

SENSITIVITY_MAP = {
    "sensitivity_soft": "-30dB",
    "sensitivity_normal": "-40dB",
    "sensitivity_aggressive": "-50dB"
}

# Χρονικά όρια για τις κλήσεις ffmpeg/ffprobe -- ώστε ένα "κολλημένο" ffmpeg
# (π.χ. λόγω κατεστραμμένου αρχείου) να μην κλειδώνει επ' άπειρον την εφαρμογή.
FFPROBE_TIMEOUT_SEC = 20
FFMPEG_TIMEOUT_SEC = 600
VISUALIZER_TIMEOUT_SEC = 60

def migrate_config(config):
    """Σημείο μετάβασης για μελλοντικές αλλαγές στη ΔΟΜΗ του config.json
    (π.χ. αν κάποτε μετονομαστεί ή αλλάξει μορφή ένα key). Προς το παρόν δεν
    χρειάζεται καμία μετατροπή -- απλά διασφαλίζει ότι το config_version
    είναι πάντα ενημερωμένο μετά το φόρτωμα."""
    old_version = config.get("config_version", 0)
    # Παράδειγμα για το μέλλον:
    # if old_version < 2:
    #     config["neo_key"] = config.pop("palio_key", DEFAULT_CONFIG["neo_key"])
    config["config_version"] = CONFIG_VERSION
    return config

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
                merged = {**DEFAULT_CONFIG, **config}
                return migrate_config(merged)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(config):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
    except Exception as e:
        # Σε "windowed" builds (χωρίς κονσόλα, π.χ. μετά από PyInstaller
        # --noconsole) το print() μπορεί να ρίξει δικό του exception επειδή
        # δεν υπάρχει stdout -- το τυλίγουμε ώστε μια αποτυχημένη αποθήκευση
        # να μην ρίξει κάτω όλη την εφαρμογή.
        try:
            print(f"Error saving config: {e}")
        except Exception:
            pass

def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

TRANSLATIONS = {
    'el': {
        'app_title': f"Audio Harmonizer Pro v{__version__}",
        'main_title': "🎵 Audio Harmonizer & Trimmer",
        'btn_settings': "⚙ Ρυθμίσεις",
        'btn_in_text': "📥 1. Επιλογή Φακέλου με Ήχους (ή Drag & Drop)",
        'lbl_in_empty': "Δεν έχει επιλεχθεί φάκελος",
        'btn_out_text': "📤 2. Επιλογή Φακέλου Προορισμού",
        'lbl_out_empty': "Δεν έχει επιλεχθεί φάκελος",
        'sensitivity_label': "Ευαισθησία Κοψίματος Σιωπής:",
        'sensitivity_soft': "Ήπιο (κόβει λιγότερο)",
        'sensitivity_normal': "Κανονικό",
        'sensitivity_aggressive': "Επιθετικό (κόβει περισσότερο)",
        'skip_existing_check': "Παράλειψη αρχείων που υπάρχουν ήδη στον προορισμό",
        'btn_preview_text': "🔍 Έλεγχος Αρχείου (Drag & Drop)",
        'lbl_status_ready': "Έτοιμο για δράση... (Σύρε αρχεία/φακέλους εδώ)",
        'btn_run_text': "🚀 Έναρξη Επεξεργασίας",
        'btn_cancel_text': "🚫 Ακύρωση",
        'settings_title': "Ρυθμίσεις",
        'settings_language_label': "Επιλογή Γλώσσας:",
        'settings_lang_el': "Ελληνικά",
        'settings_lang_en': "English",
        'export_settings_title': "Ρυθμίσεις Εξαγωγής",
        'lbl_format': "Μορφή:",
        'lbl_bitrate': "Bitrate:",
        'lbl_sample_rate': "Ρυθμός Δειγματοληψίας:",
        'sample_rate_original': "Αρχικός",
        'btn_reset_settings': "🔄 Επαναφορά Ρυθμίσεων",
        'single_file_preview_title': "🎵 1. Έλεγχος & Αναπαραγωγή (Preview)",
        'batch_folder_title': "📁 2. Επεξεργασία Πολλαπλών (Φάκελος)",
        'btn_play_orig': "▶ Αρχικό",
        'btn_play_trimmed': "▶ Επεξεργασμένο",
        'btn_stop_audio': "⏹ Σταμάτημα",
        # Popups & Dialogs
        'msg_unsupported_title': "Μη υποστηριζόμενο αρχείο",
        'msg_unsupported_body': "Παρακαλώ σύρετε αρχείο ήχου (MP3, WAV, FLAC, M4A, AAC, OGG).",
        'msg_preview_done_title': "Preview Έτοιμο",
        'msg_preview_done_body': "Το preview δημιουργήθηκε επιτυχώς!",
        'msg_preview_err_title': "Σφάλμα Preview",
        'msg_preview_err_body': "Απέτυχε η επεξεργασία:",
        'msg_playback_err_title': "Σφάλμα Αναπαραγωγής",
        'msg_playback_err_body': "Αποτυχία αναπαραγωγής:",
        'msg_ffmpeg_err_title': "Σφάλμα FFmpeg",
        'msg_ffmpeg_err_body': "Δεν βρέθηκε το ffmpeg.exe στη διαδρομή:\n",
        'msg_no_files_title': "Προσοχή",
        'msg_no_files_body': "Δεν βρέθηκαν υποστηριζόμενα αρχεία ήχου στον φάκελο εισόδου!",
        'msg_completed_title': "Ολοκληρώθηκε",
        'msg_cancelled_title': "Ακυρώθηκε",
        'msg_completed_body_success': "Αλέστηκαν επιτυχώς:",
        'msg_completed_body_skipped': "Παραλείφθηκαν (ήδη υπήρχαν):",
        'msg_completed_body_out': "Αποθηκεύτηκαν στον φάκελο:",
        'lbl_config_location': "💾 Οι ρυθμίσεις αποθηκεύονται στο:",
        'msg_all_skipped_title': "Δεν χρειάστηκε τίποτα",
        'msg_all_skipped_body': "Όλα τα αρχεία υπήρχαν ήδη στον φάκελο προορισμού — δεν έγινε καμία νέα επεξεργασία.\nΠαραλείφθηκαν:"
    },
    'en': {
        'app_title': f"Audio Harmonizer Pro v{__version__}",
        'main_title': "🎵 Audio Harmonizer & Trimmer",
        'btn_settings': "⚙ Settings",
        'btn_in_text': "📥 1. Select Audio Input Folder (or Drag & Drop)",
        'lbl_in_empty': "No folder selected",
        'btn_out_text': "📤 2. Select Destination Folder",
        'lbl_out_empty': "No folder selected",
        'sensitivity_label': "Silence Trimming Sensitivity:",
        'sensitivity_soft': "Mild (trims less)",
        'sensitivity_normal': "Normal",
        'sensitivity_aggressive': "Aggressive (trims more)",
        'skip_existing_check': "Skip files that already exist in destination",
        'btn_preview_text': "🔍 Scan File (Drag & Drop)",
        'lbl_status_ready': "Ready for action... (Drop files/folders here)",
        'btn_run_text': "🚀 Start Processing",
        'btn_cancel_text': "🚫 Cancel",
        'settings_title': "Settings",
        'settings_language_label': "Select Language:",
        'settings_lang_el': "Greek",
        'settings_lang_en': "English",
        'export_settings_title': "Export Settings",
        'lbl_format': "Format:",
        'lbl_bitrate': "Bitrate:",
        'lbl_sample_rate': "Sample Rate:",
        'sample_rate_original': "Original",
        'btn_reset_settings': "🔄 Reset Settings",
        'single_file_preview_title': "🎵 1. Check & Playback (Preview)",
        'batch_folder_title': "📁 2. Process Multiple Files (Folder)",
        'btn_play_orig': "▶ Original",
        'btn_play_trimmed': "▶ Trimmed",
        'btn_stop_audio': "⏹ Stop",
        # Popups & Dialogs
        'msg_unsupported_title': "Unsupported File",
        'msg_unsupported_body': "Please drop a valid audio file (MP3, WAV, FLAC, M4A, AAC, OGG).",
        'msg_preview_done_title': "Preview Ready",
        'msg_preview_done_body': "Preview created successfully!",
        'msg_preview_err_title': "Preview Error",
        'msg_preview_err_body': "Processing failed:",
        'msg_playback_err_title': "Playback Error",
        'msg_playback_err_body': "Failed to play audio:",
        'msg_ffmpeg_err_title': "FFmpeg Error",
        'msg_ffmpeg_err_body': "ffmpeg.exe was not found at path:\n",
        'msg_no_files_title': "Warning",
        'msg_no_files_body': "No supported audio files found in the input folder!",
        'msg_completed_title': "Completed",
        'msg_cancelled_title': "Cancelled",
        'msg_completed_body_success': "Successfully processed:",
        'msg_completed_body_skipped': "Skipped (already existed):",
        'msg_completed_body_out': "Saved to folder:",
        'lbl_config_location': "💾 Settings are saved at:",
        'msg_all_skipped_title': "Nothing to do",
        'msg_all_skipped_body': "All files already existed in the destination folder — nothing new was processed.\nSkipped:"
    }
}

def build_envelope_from_pcm(raw_bytes, sample_rate=11025, num_bars=10, window_ms=60):
    """
    Μετατρέπει ωμό PCM ήχο (mono, 16-bit) σε μια χρονική ακολουθία εντάσεων
    ανά μπάρα (1-10), με βάση την ΠΡΑΓΜΑΤΙΚΗ ενέργεια του ήχου -- ώστε το
    Equalizer να παλμογραφεί σύμφωνα με το πραγματικό κομμάτι που παίζει,
    αντί για τυχαία (fake) κίνηση.

    Με numpy: γίνεται πραγματική ανάλυση φάσματος (FFT) ανά χρονικό
    παράθυρο και οι συχνότητες χωρίζονται σε `num_bars` λογαριθμικές ζώνες
    (bass -> treble), όπως σε πραγματικό equalizer. Χρησιμοποιούμε την
    ΚΟΡΥΦΗ (max) κάθε ζώνης -- όχι τον μέσο όρο -- γιατί οι ψηλές ζώνες
    (πλατύτερες σε Hz) περιέχουν πολύ περισσότερα "άδεια" bins του FFT και ο
    μέσος όρος θα τις έδειχνε τεχνητά πιο αδύναμες απ' ό,τι πραγματικά είναι.

    Χωρίς numpy: γίνεται fallback σε καθαρή ανάλυση έντασης (RMS) ανά
    παράθυρο. Παραμένει 100% καθοδηγούμενο από τον πραγματικό ήχο, απλά
    χωρίς διαχωρισμό σε ζώνες συχνότητας.

    Επιστρέφει: (envelope, window_ms) όπου το envelope είναι μια λίστα από
    λίστες μήκους num_bars, μία ανά χρονικό παράθυρο.
    """
    if not raw_bytes:
        return [], window_ms

    samples = array.array('h')
    usable_len = len(raw_bytes) - (len(raw_bytes) % 2)
    if usable_len <= 0:
        return [], window_ms
    samples.frombytes(raw_bytes[:usable_len])
    if sys.byteorder == 'big':
        samples.byteswap()

    window_size = max(1, int(sample_rate * (window_ms / 1000.0)))
    total_samples = len(samples)
    if total_samples < window_size:
        return [], window_ms

    n_windows = total_samples // window_size

    if HAS_NUMPY:
        arr = np.frombuffer(samples, dtype=np.int16).astype(np.float32)
        arr = arr[: n_windows * window_size].reshape(n_windows, window_size)
        window_fn = np.hanning(window_size)
        nyquist = sample_rate / 2.0
        band_edges = np.geomspace(50, max(51, nyquist * 0.98), num_bars + 1)
        freqs = np.fft.rfftfreq(window_size, d=1.0 / sample_rate)

        raw_bands = []
        max_band_energy = 1e-6
        for w in arr:
            spectrum = np.abs(np.fft.rfft(w * window_fn))
            band_vals = []
            for b in range(num_bars):
                mask = (freqs >= band_edges[b]) & (freqs < band_edges[b + 1])
                band_vals.append(float(spectrum[mask].max()) if mask.any() else 0.0)
            raw_bands.append(band_vals)
            local_max = max(band_vals) if band_vals else 0.0
            if local_max > max_band_energy:
                max_band_energy = local_max

        ref = max(max_band_energy, 1e-6)
        envelope = [
            [max(1, min(10, int(round((v / ref) * 10)))) for v in band_vals]
            for band_vals in raw_bands
        ]
        return envelope, window_ms

    # ---- Fallback χωρίς numpy: πραγματική ένταση (RMS) ανά παράθυρο ----
    raw_rms = []
    max_rms = 1e-6
    for i in range(n_windows):
        chunk = samples[i * window_size:(i + 1) * window_size]
        if not chunk:
            raw_rms.append(0.0)
            continue
        sq_sum = sum(s * s for s in chunk)
        rms = (sq_sum / len(chunk)) ** 0.5
        raw_rms.append(rms)
        if rms > max_rms:
            max_rms = rms

    # Σταθερό (όχι τυχαίο) "προφίλ" ανά μπάρα, ώστε να μη φαίνονται όλες
    # ίδιες -- η κίνηση παραμένει 100% καθοδηγούμενη από την πραγματική
    # ένταση του κομματιού.
    bar_profile = [0.55, 0.7, 0.85, 1.0, 1.0, 0.95, 0.85, 0.7, 0.6, 0.5]
    ref = max(max_rms, 1e-6)
    envelope = []
    for rms in raw_rms:
        level = (rms / ref) * 10
        envelope.append([
            max(1, min(10, int(round(level * bar_profile[b % len(bar_profile)]))))
            for b in range(num_bars)
        ])
    return envelope, window_ms


class AudioVisualizer(tk.Canvas):
    def __init__(self, master, width=140, height=35, **kwargs):
        super().__init__(master, width=width, height=height, bg="#1e1e1e", highlightthickness=0, **kwargs)
        self.bars = 10
        self.is_playing = False
        self.bar_values = [1] * self.bars
        self.data_source = None  # callable -> λίστα εντάσεων (1-10) ή None
        self.refresh_ms = 60
        self.draw_bars()

    def draw_bars(self):
        self.delete("all")
        w, h = self.winfo_width() or 140, self.winfo_height() or 35
        bar_w = (w - (self.bars + 1) * 2) / self.bars

        for i in range(self.bars):
            val = self.bar_values[i] if self.is_playing else 1
            x0 = 2 + i * (bar_w + 2)
            x1 = x0 + bar_w

            segments = 8
            active_seg = int((val / 10) * segments)
            for s in range(segments):
                y1 = h - (s * (h / segments))
                y0 = y1 - (h / segments) + 1
                color = "#2a2a2a"
                if s < active_seg:
                    color = "#2ecc71" if s < 5 else ("#f1c40f" if s < 7 else "#e67e22")
                self.create_rectangle(x0, y0, x1, y1, fill=color, outline="")

    def animate(self):
        if not self.is_playing:
            self.bar_values = [1] * self.bars
            self.draw_bars()
            return

        values = None
        if self.data_source:
            try:
                values = self.data_source()
            except Exception:
                values = None

        if values:
            # Ομαλή μετάβαση προς τις πραγματικές τιμές (πιο φυσική κίνηση,
            # όχι απότομο "τρέμουλο")
            self.bar_values = [
                max(1, min(10, int(round((old * 0.35) + (new * 0.65)))))
                for old, new in zip(self.bar_values, values)
            ]
        else:
            # Η ανάλυση του ήχου δεν έχει ετοιμαστεί ακόμα -> ήρεμη πτώση
            # (όχι τυχαία κίνηση) μέχρι να έρθουν πραγματικά δεδομένα.
            self.bar_values = [max(1, v - 1) for v in self.bar_values]

        self.draw_bars()
        self.after(self.refresh_ms, self.animate)

    def start(self):
        if not self.is_playing:
            self.is_playing = True
            self.animate()

    def stop(self):
        self.is_playing = False
        self.draw_bars()

class MP3ProcessorApp:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.current_lang = self.config.get("language", "el")
        
        self.ui_elements = {}
        self.input_dir = ""
        self.output_dir = ""
        self.cancel_requested = False
        self.processing = False
        self.settings_window = None

        self.last_preview_orig = ""
        self.last_preview_trimmed = ""

        # Equalizer: κατάσταση για την πραγματική ανάλυση ήχου (όχι fake animation)
        self.visualizer_job_id = 0
        self.visualizer_envelope = []
        self.visualizer_window_ms = 60
        self.visualizer_ready = False

        # Saved Config Variables
        self.export_format_var = tk.StringVar(value=self.config.get("export_format", "MP3"))
        self.export_bitrate_var = tk.StringVar(value=self.config.get("export_bitrate", "320k"))
        
        sr_config = self.config.get("export_samplerate", "Original")
        sr_initial = self.translate('sample_rate_original') if sr_config in ["Original", "Αρχικός"] else sr_config
        self.export_samplerate_var = tk.StringVar(value=sr_initial)

        self.root.title(self.translate('app_title'))
        self.root.geometry("560x650")
        self.root.minsize(520, 600)
        self.root.config(bg="#1e1e1e")
        self.root.resizable(True, True)

        try:
            self.root.iconbitmap(get_resource_path("icon.ico"))
        except Exception:
            pass

        # Drag & Drop Setup
        if HAS_DND:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind('<<Drop>>', self.handle_drop)
            # Το tkdnd/tkinterdnd2 ονομάζει τα enter/leave events του DROP
            # TARGET "<<DropEnter>>"/"<<DropLeave>>" (το "<<DragEnter>>" αφορά
            # την πλευρά που ΞΕΚΙΝΑΕΙ ένα drag, όχι αυτή που το δέχεται) -- γι'
            # αυτό δεν φώτιζε ποτέ τίποτα, ούτε πριν. Δένουμε και τα δύο
            # ονόματα για συμβατότητα με διαφορετικές εκδόσεις: ό,τι όνομα δεν
            # αναγνωρίζεται απλά δεν πυροδοτείται ποτέ, δεν πειράζει τίποτα.
            self.root.dnd_bind('<<DropEnter>>', self.on_drag_enter)
            self.root.dnd_bind('<<DragEnter>>', self.on_drag_enter)
            self.root.dnd_bind('<<DropPosition>>', self.on_drag_enter)
            self.root.dnd_bind('<<DropLeave>>', self.on_drag_leave)
            self.root.dnd_bind('<<DragLeave>>', self.on_drag_leave)

        # Header Frame
        top_frame = tk.Frame(root, bg="#1e1e1e")
        top_frame.pack(fill="x", pady=10, padx=10)

        self.title_label = tk.Label(
            top_frame, text="", fg="#00ffcc", bg="#1e1e1e", font=("Segoe UI", 14, "bold")
        )
        self.title_label.pack(side="left", padx=(10, 0), expand=True, anchor="w")
        self.ui_elements['title_label'] = self.title_label

        self.settings_btn = tk.Button(
            top_frame, text=self.translate('btn_settings'), command=self.open_settings_window,
            bg="#520000", fg="white", font=("Segoe UI", 9, "bold"),
            relief="flat", cursor="hand2", padx=8, pady=3, activebackground="#a50000", activeforeground="white"
        )
        self.settings_btn.pack(side="right", padx=(0, 10))
        self.ui_elements['settings_btn'] = self.settings_btn

        # Main Container
        main_container = tk.Frame(root, bg="#1e1e1e")
        main_container.pack(fill="both", expand=True, padx=15, pady=5)

        # 1. Single Track Section
        self.single_frame = tk.LabelFrame(
            main_container, text=self.translate('single_file_preview_title'),
            bg="#1e1e1e", fg="#3b82f6", font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=10, pady=8
        )
        self.single_frame.pack(fill="x", pady=5)
        self.ui_elements['single_frame'] = self.single_frame

        prev_top_frame = tk.Frame(self.single_frame, bg="#1e1e1e")
        prev_top_frame.pack(fill="x")

        self.btn_preview = tk.Button(
            prev_top_frame, text=self.translate('btn_preview_text'), command=self.run_preview,
            bg="#1f538d", fg="white", font=("Segoe UI", 10, "bold"),
            width=28, pady=4, relief="flat", cursor="hand2", activebackground="#2563eb", activeforeground="white"
        )
        self.btn_preview.pack(pady=4)
        self.lbl_file_info = tk.Label(
            prev_top_frame, text="", fg="#00ffcc", bg="#1e1e1e", font=("Segoe UI", 8, "bold")
        )
        self.lbl_file_info.pack(pady=2)
        self.ui_elements['btn_preview'] = self.btn_preview

        # Player Frame
        player_frame = tk.Frame(self.single_frame, bg="#111111", padx=5, pady=4)
        player_frame.pack(fill="x", pady=(4, 2))

        self.btn_play_orig = tk.Button(
            player_frame, text=self.translate('btn_play_orig'), command=lambda: self.play_audio(self.last_preview_orig),
            bg="#333333", fg="#00ffcc", font=("Segoe UI", 8, "bold"), state="disabled", relief="flat", cursor="hand2", width=12
        )
        self.btn_play_orig.pack(side="left", padx=4)
        self.ui_elements['btn_play_orig'] = self.btn_play_orig

        self.btn_play_trimmed = tk.Button(
            player_frame, text=self.translate('btn_play_trimmed'), command=lambda: self.play_audio(self.last_preview_trimmed),
            bg="#333333", fg="#00ffcc", font=("Segoe UI", 8, "bold"), state="disabled", relief="flat", cursor="hand2", width=14
        )
        self.btn_play_trimmed.pack(side="left", padx=4)
        self.ui_elements['btn_play_trimmed'] = self.btn_play_trimmed

        self.btn_stop = tk.Button(
            player_frame, text=self.translate('btn_stop_audio'), command=self.stop_audio,
            bg="#552222", fg="white", font=("Segoe UI", 8, "bold"), state="disabled", relief="flat", cursor="hand2", width=10
        )
        self.btn_stop.pack(side="right", padx=4)
        self.ui_elements['btn_stop'] = self.btn_stop

        self.visualizer = AudioVisualizer(player_frame, width=140, height=30)
        self.visualizer.pack(side="right", padx=10)
        self.visualizer.data_source = self.get_current_bar_values

        # 2. Batch Section
        self.batch_frame = tk.LabelFrame(
            main_container, text=self.translate('batch_folder_title'),
            bg="#1e1e1e", fg="#f59e0b", font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=10, pady=8
        )
        self.batch_frame.pack(fill="x", pady=5)
        self.ui_elements['batch_frame'] = self.batch_frame

        self.btn_in = tk.Button(
            self.batch_frame, text=self.translate('btn_in_text'), command=self.select_input,
            bg="#965000", fg="white", font=("Segoe UI", 10, "bold"),
            width=42, pady=5, relief="flat", cursor="hand2", activebackground="#965000", activeforeground="white"
        )
        self.btn_in.pack(pady=2)
        self.ui_elements['btn_in'] = self.btn_in

        self.lbl_in = tk.Label(self.batch_frame, text=self.translate('lbl_in_empty'), fg="#888888", bg="#1e1e1e", font=("Segoe UI", 8))
        self.lbl_in.pack(pady=1)
        self.ui_elements['lbl_in'] = self.lbl_in

        self.btn_out = tk.Button(
            self.batch_frame, text=self.translate('btn_out_text'), command=self.select_output,
            bg="#965000", fg="white", font=("Segoe UI", 10, "bold"),
            width=42, pady=5, relief="flat", cursor="hand2", activebackground="#965000", activeforeground="white"
        )
        self.btn_out.pack(pady=4)
        self.ui_elements['btn_out'] = self.btn_out

        self.lbl_out = tk.Label(self.batch_frame, text=self.translate('lbl_out_empty'), fg="#888888", bg="#1e1e1e", font=("Segoe UI", 8))
        self.lbl_out.pack(pady=1)
        self.ui_elements['lbl_out'] = self.lbl_out

        # Sensitivity Settings
        settings_frame = tk.Frame(self.batch_frame, bg="#1e1e1e")
        settings_frame.pack(pady=6)

        self.lbl_sensitivity = tk.Label(settings_frame, text=self.translate('sensitivity_label'), fg="#cccccc", bg="#1e1e1e", font=("Segoe UI", 9))
        self.lbl_sensitivity.grid(row=0, column=0, sticky="w", padx=5)
        self.ui_elements['lbl_sensitivity'] = self.lbl_sensitivity

        self.sensitivity_keys = ['sensitivity_soft', 'sensitivity_normal', 'sensitivity_aggressive']
        saved_sens_key = self.config.get("sensitivity", "sensitivity_normal")
        self.sensitivity_var = tk.StringVar(value=self.translate(saved_sens_key))
        
        sensitivity_combo = ttk.Combobox(
            settings_frame, textvariable=self.sensitivity_var,
            values=[self.translate(k) for k in self.sensitivity_keys], state="readonly", width=25
        )
        sensitivity_combo.grid(row=0, column=1, padx=5)
        sensitivity_combo.bind("<<ComboboxSelected>>", self.on_sensitivity_change)
        self.sensitivity_combo = sensitivity_combo
        self.ui_elements['sensitivity_combo'] = self.sensitivity_combo

        # Skip Checkbox
        self.skip_existing_var = tk.BooleanVar(value=self.config.get("skip_existing", True))
        self.skip_check = tk.Checkbutton(
            self.batch_frame, text=self.translate('skip_existing_check'), variable=self.skip_existing_var,
            command=self.save_current_config, fg="#cccccc", bg="#1e1e1e", selectcolor="#333333",
            activebackground="#1e1e1e", activeforeground="#cccccc", font=("Segoe UI", 8)
        )
        self.skip_check.pack(pady=2)
        self.ui_elements['skip_check'] = self.skip_check

        # Action Buttons
        btns_frame = tk.Frame(self.batch_frame, bg="#1e1e1e")
        btns_frame.pack(pady=6)

        self.btn_run = tk.Button(
            btns_frame, text=self.translate('btn_run_text'), command=self.start_thread,
            bg="#444444", fg="#777777", font=("Segoe UI", 10, "bold"),
            width=18, pady=5, relief="flat", cursor="arrow", state="disabled"
        )
        self.btn_run.grid(row=0, column=0, padx=5)
        self.ui_elements['btn_run'] = self.btn_run

        self.btn_cancel = tk.Button(
            btns_frame, text=self.translate('btn_cancel_text'), command=self.request_cancel,
            bg="#661111", fg="white", font=("Segoe UI", 9, "bold"),
            width=14, pady=5, relief="flat", cursor="hand2", state="disabled"
        )
        self.btn_cancel.grid(row=0, column=1, padx=5)
        self.ui_elements['btn_cancel'] = self.btn_cancel

        # Status & Progress Bar
        self.lbl_status = tk.Label(main_container, text=self.translate('lbl_status_ready'), fg="#aaaaaa", bg="#1e1e1e", font=("Segoe UI", 9, "italic"))
        self.lbl_status.pack(pady=4)
        self.ui_elements['lbl_status'] = self.lbl_status

        self.progress = ttk.Progressbar(main_container, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=2)

        self.lbl_eta = tk.Label(main_container, text="", fg="#888888", bg="#1e1e1e", font=("Segoe UI", 8))
        self.lbl_eta.pack(pady=1)
        self.ui_elements['lbl_eta'] = self.lbl_eta

        # Log box
        self.log_box = tk.Text(main_container, height=4, bg="#111111", fg="#00ff88", font=("Consolas", 8), relief="flat")
        self.log_box.pack(fill="both", expand=True, pady=4)
        self.log_box.config(state="disabled")

        self.apply_translations()

    # ---------- Drag & Drop Handler ----------

    def _extract_first_dnd_path(self, data):
        """Μετατρέπει το raw string ενός drag & drop event σε ένα καθαρό
        path, μέσω του Tcl list parser (χειρίζεται σωστά paths με κενά)."""
        if not data:
            return ""
        data = data.strip()
        try:
            paths = self.root.tk.splitlist(data)
        except Exception:
            paths = [data]
        if not paths:
            return ""
        first = paths[0]
        if first.startswith('{') and first.endswith('}'):
            first = first[1:-1]
        return first

    def handle_drop(self, event):
        self.on_drag_leave()
        if self.processing:
            # Μην αλλάξεις input/output ενώ τρέχει ήδη μια επεξεργασία batch.
            return

        path = self._extract_first_dnd_path(getattr(event, 'data', ''))
        if not path:
            return

        if os.path.isdir(path):
            self.input_dir = path
            display_name = os.path.basename(path) or path
            self.lbl_in.config(text=f"📥 {display_name}", fg="#00ffcc")
            if not self.output_dir:
                self.output_dir = os.path.join(path, "Harmonized_Output")
                self.lbl_out.config(text=f"📤 Harmonized_Output", fg="#00ffcc")
            self.check_ready()
            self.log(f"📂 Drag & Drop Folder Selected: {display_name}")
        elif os.path.isfile(path):
            valid_exts = ('.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg')
            if path.lower().endswith(valid_exts):
                info_text = self.get_audio_info(path)
                self.lbl_file_info.config(text=info_text)
                self.run_preview(filepath=path)
            else:
                messagebox.showwarning(
                    self.translate('msg_unsupported_title'),
                    self.translate('msg_unsupported_body')
                )

    def on_drag_enter(self, event):
        # Φωτίζουμε ΜΟΝΟ το σχετικό section: πάνω (Preview) αν σέρνεται
        # αρχείο, κάτω (Batch) αν σέρνεται φάκελος. Σε ΚΑΘΕ περίπτωση που δεν
        # είμαστε ΣΙΓΟΥΡΟΙ τι σέρνεται -- είτε επειδή δεν δόθηκαν καθόλου
        # δεδομένα στο DragEnter, είτε επειδή δόθηκε κάτι που δεν αντιστοιχεί
        # σε υπαρκτό αρχείο/φάκελο ακόμα (πολύ συνηθισμένο στα Windows: το
        # DragEnter συχνά δεν δίνει το τελικό, ολοκληρωμένο path όπως το
        # Drop) -- φωτίζονται και τα δύο, όπως πριν. Έτσι δεν "σβήνει" ποτέ η
        # ένδειξη, ακόμα κι αν δεν μπορούμε να αναγνωρίσουμε τον τύπο νωρίς.
        path = self._extract_first_dnd_path(getattr(event, 'data', ''))
        is_folder = bool(path) and os.path.isdir(path)
        is_file = bool(path) and os.path.isfile(path)
        known = is_folder or is_file

        if hasattr(self, 'single_frame'):
            if is_file or not known:
                self.single_frame.config(highlightbackground="#00ffcc", highlightthickness=2)
            else:
                self.single_frame.config(highlightthickness=0)

        if hasattr(self, 'batch_frame'):
            if is_folder or not known:
                self.batch_frame.config(highlightbackground="#f59e0b", highlightthickness=2)
            else:
                self.batch_frame.config(highlightthickness=0)

    def on_drag_leave(self, event=None):
        if hasattr(self, 'single_frame'):
            self.single_frame.config(highlightthickness=0)
        if hasattr(self, 'batch_frame'):
            self.batch_frame.config(highlightthickness=0)

    # ---------- Audio Player Logic ----------

    def play_audio(self, filepath):
        if not HAS_PYGAME or not filepath or not os.path.exists(filepath):
            return
        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            self.btn_stop.config(state="normal", bg="#aa2222")

            if hasattr(self, 'visualizer'):
                # Ξεκινάει η ανάλυση του ΠΡΑΓΜΑΤΙΚΟΥ ήχου σε background thread
                # (ώστε να μην καθυστερεί η αναπαραγωγή) και το Equalizer θα
                # συγχρονιστεί μαζί της μόλις είναι έτοιμη.
                self.visualizer_job_id += 1
                job_id = self.visualizer_job_id
                self.visualizer_ready = False
                self.visualizer_envelope = []
                self.visualizer.start()
                threading.Thread(
                    target=self._analyze_visualizer_worker, args=(filepath, job_id), daemon=True
                ).start()

            self.check_audio_end()

        except Exception as e:
            messagebox.showerror(
                self.translate('msg_playback_err_title'),
                f"{self.translate('msg_playback_err_body')}\n{e}"
            )

    def get_current_bar_values(self):
        """Οι τρέχουσες εντάσεις (1-10) ανά μπάρα του Equalizer, με βάση το
        πραγματικό σημείο αναπαραγωγής του κομματιού που παίζει. Επιστρέφει
        None όσο η ανάλυση δεν έχει ολοκληρωθεί ακόμα ή δεν παίζει τίποτα."""
        if not self.visualizer_ready or not self.visualizer_envelope:
            return None
        if not HAS_PYGAME:
            return None
        try:
            pos_ms = pygame.mixer.music.get_pos()
        except Exception:
            return None
        if pos_ms is None or pos_ms < 0:
            return None

        envelope = self.visualizer_envelope
        idx = int(pos_ms / self.visualizer_window_ms)
        if idx >= len(envelope):
            idx = len(envelope) - 1
        if idx < 0:
            return None
        return envelope[idx]

    def _analyze_visualizer_worker(self, filepath, job_id):
        try:
            envelope, window_ms = self._build_audio_envelope(filepath)
        except Exception:
            envelope, window_ms = [], self.visualizer_window_ms

        if job_id != self.visualizer_job_id:
            # Ο χρήστης πάτησε play σε άλλο κομμάτι στο μεταξύ -- αγνόησε
            # αυτό το (πλέον παλιό) αποτέλεσμα.
            return

        self.visualizer_envelope = envelope
        self.visualizer_window_ms = window_ms
        self.visualizer_ready = bool(envelope)

    def _build_audio_envelope(self, filepath, num_bars=10, window_ms=60, sample_rate=11025):
        """Αποκωδικοποιεί το αρχείο σε ωμό PCM μέσω ffmpeg και υπολογίζει το
        πραγματικό envelope έντασης/συχνοτήτων για το Equalizer
        (βλ. build_envelope_from_pcm)."""
        ffmpeg_path = self.get_ffmpeg_path()
        if not os.path.exists(ffmpeg_path) or not os.path.exists(filepath):
            return [], window_ms

        creationflags = 0x08000000 if os.name == 'nt' else 0
        cmd = [
            ffmpeg_path, "-v", "error", "-i", filepath,
            "-f", "s16le", "-ac", "1", "-ar", str(sample_rate), "pipe:1"
        ]
        try:
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags,
                timeout=VISUALIZER_TIMEOUT_SEC
            )
        except Exception:
            return [], window_ms

        return build_envelope_from_pcm(
            result.stdout, sample_rate=sample_rate, num_bars=num_bars, window_ms=window_ms
        )

    def stop_audio(self):
        if HAS_PYGAME:
            pygame.mixer.music.stop()
            self.btn_stop.config(state="disabled", bg="#552222")
            if hasattr(self, 'visualizer'):
                self.visualizer.stop()

    def check_audio_end(self):
        if HAS_PYGAME and pygame.mixer.music.get_busy():
            self.root.after(200, self.check_audio_end)
        else:
            self.stop_audio()

    def play_finish_sound(self):
        try:
            winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            pass

    # ---------- Persistence Methods ----------

    def save_current_config(self):
        self.config["language"] = self.current_lang
        self.config["export_format"] = self.export_format_var.get()
        self.config["export_bitrate"] = self.export_bitrate_var.get()
        
        sr_val = self.export_samplerate_var.get()
        if sr_val in ["Original", "Αρχικός"]:
            self.config["export_samplerate"] = "Original"
        else:
            self.config["export_samplerate"] = sr_val
            
        self.config["skip_existing"] = self.skip_existing_var.get()
        save_config(self.config)

    def on_sensitivity_change(self, event=None):
        idx = self.sensitivity_combo.current()
        if idx >= 0:
            self.config["sensitivity"] = self.sensitivity_keys[idx]
            save_config(self.config)

    def reset_to_defaults(self):
        self.config = DEFAULT_CONFIG.copy()
        save_config(self.config)
        self.current_lang = self.config["language"]
        self.export_format_var.set(self.config["export_format"])
        self.export_bitrate_var.set(self.config["export_bitrate"])
        self.export_samplerate_var.set(self.translate('sample_rate_original'))
        self.sensitivity_var.set(self.translate(self.config["sensitivity"]))
        self.skip_existing_var.set(self.config["skip_existing"])
        self.apply_translations()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.destroy()
            self.open_settings_window()

    # ---------- Translation methods ----------

    def translate(self, key):
        return TRANSLATIONS.get(self.current_lang, TRANSLATIONS['en']).get(key, key)

    def apply_translations(self):
        idx = 1
        if hasattr(self, 'sensitivity_combo') and self.sensitivity_combo:
            idx = self.sensitivity_combo.current()

        idx_sr = 0
        if 'samplerate_combo' in self.ui_elements and self.ui_elements['samplerate_combo'].winfo_exists():
            c_idx = self.ui_elements['samplerate_combo'].current()
            if c_idx >= 0:
                idx_sr = c_idx

        self.root.title(self.translate('app_title'))
        
        mapping = {
            'title_label': 'main_title',
            'settings_btn': 'btn_settings',
            'btn_in': 'btn_in_text',
            'btn_out': 'btn_out_text',
            'lbl_sensitivity': 'sensitivity_label',
            'skip_check': 'skip_existing_check',
            'btn_preview': 'btn_preview_text',
            'btn_run': 'btn_run_text',
            'btn_cancel': 'btn_cancel_text',
            'lbl_status': 'lbl_status_ready',
            'lbl_settings_lang': 'settings_language_label',
            'export_frame': 'export_settings_title',
            'lbl_format': 'lbl_format',
            'lbl_bitrate': 'lbl_bitrate',
            'lbl_sample_rate': 'lbl_sample_rate',
            'single_frame': 'single_file_preview_title',
            'batch_frame': 'batch_folder_title',
            'btn_reset': 'btn_reset_settings',
            'btn_play_orig': 'btn_play_orig',
            'btn_play_trimmed': 'btn_play_trimmed',
            'btn_stop': 'btn_stop_audio'
        }
        
        for ui_key, trans_key in mapping.items():
            if ui_key in self.ui_elements:
                widget = self.ui_elements[ui_key]
                if widget and widget.winfo_exists():
                    widget.config(text=self.translate(trans_key))

        if 'sensitivity_combo' in self.ui_elements:
            new_values = [self.translate(k) for k in self.sensitivity_keys]
            self.ui_elements['sensitivity_combo'].config(values=new_values)
            if idx >= 0:
                self.ui_elements['sensitivity_combo'].current(idx)

        if 'samplerate_combo' in self.ui_elements and self.ui_elements['samplerate_combo'].winfo_exists():
            new_sr_values = [self.translate('sample_rate_original'), "44100 Hz", "48000 Hz"]
            self.ui_elements['samplerate_combo'].config(values=new_sr_values)
            if idx_sr >= 0:
                self.ui_elements['samplerate_combo'].current(idx_sr)
            if idx_sr == 0:
                self.export_samplerate_var.set(self.translate('sample_rate_original'))

        if 'lbl_config_path' in self.ui_elements:
            w = self.ui_elements['lbl_config_path']
            if w and w.winfo_exists():
                w.config(text=f"{self.translate('lbl_config_location')}\n{CONFIG_FILE}")

    def open_settings_window(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return

        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title(self.translate('settings_title'))
        self.settings_window.configure(bg="#1e1e1e")
        self.settings_window.resizable(False, False)
        
        x = self.root.winfo_x() + 60
        y = self.root.winfo_y() + 60
        self.settings_window.geometry(f"360x420+{x}+{y}")

        # Language Selection
        lbl_settings_lang = tk.Label(
            self.settings_window, text=self.translate('settings_language_label'),
            bg="#1e1e1e", fg="white", font=("Segoe UI", 10, "bold")
        )
        lbl_settings_lang.pack(anchor="w", padx=20, pady=(15, 8))
        self.ui_elements['lbl_settings_lang'] = lbl_settings_lang

        lang_btns_frame = tk.Frame(self.settings_window, bg="#1e1e1e")
        lang_btns_frame.pack(fill="x", padx=20, pady=(0, 10))

        btn_lang_el = tk.Button(
            lang_btns_frame, text=self.translate('settings_lang_el'), command=lambda: self.update_language('el'),
            bg="#0C1154", fg="white", font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", width=12, pady=4
        )
        btn_lang_el.pack(side="left", padx=(0, 10))

        btn_lang_en = tk.Button(
            lang_btns_frame, text=self.translate('settings_lang_en'), command=lambda: self.update_language('en'),
            bg="#3D1717", fg="white", font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", width=12, pady=4
        )
        btn_lang_en.pack(side="left")

        sep = ttk.Separator(self.settings_window, orient="horizontal")
        sep.pack(fill="x", padx=20, pady=5)

        # Export Settings Frame
        export_frame = tk.LabelFrame(
            self.settings_window, text=self.translate('export_settings_title'),
            bg="#1e1e1e", fg="#00ffcc", font=("Segoe UI", 10, "bold"), bd=1, relief="solid", padx=15, pady=12
        )
        export_frame.pack(fill="x", padx=20, pady=5)
        self.ui_elements['export_frame'] = export_frame

        # Formats
        lbl_format = tk.Label(export_frame, text=self.translate('lbl_format'), bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9))
        lbl_format.grid(row=0, column=0, sticky="w", pady=6)
        self.ui_elements['lbl_format'] = lbl_format

        format_combo = ttk.Combobox(
            export_frame, textvariable=self.export_format_var,
            values=["MP3", "WAV", "FLAC", "M4A", "AAC", "OGG"], state="readonly", width=16
        )
        format_combo.grid(row=0, column=1, sticky="e", padx=(10, 0), pady=6)
        format_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_config())
        self.ui_elements['format_combo'] = format_combo

        # Bitrate
        lbl_bitrate = tk.Label(export_frame, text=self.translate('lbl_bitrate'), bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9))
        lbl_bitrate.grid(row=1, column=0, sticky="w", pady=6)
        self.ui_elements['lbl_bitrate'] = lbl_bitrate

        bitrate_combo = ttk.Combobox(
            export_frame, textvariable=self.export_bitrate_var,
            values=["128k", "192k", "256k", "320k"], state="readonly", width=16
        )
        bitrate_combo.grid(row=1, column=1, sticky="e", padx=(10, 0), pady=6)
        bitrate_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_config())
        self.ui_elements['bitrate_combo'] = bitrate_combo

        # Sample Rate
        lbl_sample_rate = tk.Label(export_frame, text=self.translate('lbl_sample_rate'), bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9))
        lbl_sample_rate.grid(row=2, column=0, sticky="w", pady=6)
        self.ui_elements['lbl_sample_rate'] = lbl_sample_rate

        samplerate_combo = ttk.Combobox(
            export_frame, textvariable=self.export_samplerate_var,
            values=[self.translate('sample_rate_original'), "44100 Hz", "48000 Hz"], state="readonly", width=16
        )
        samplerate_combo.grid(row=2, column=1, sticky="e", padx=(10, 0), pady=6)
        samplerate_combo.bind("<<ComboboxSelected>>", lambda e: self.save_current_config())
        self.ui_elements['samplerate_combo'] = samplerate_combo

        export_frame.columnconfigure(0, weight=1)
        export_frame.columnconfigure(1, weight=1)

        btn_reset = tk.Button(
            self.settings_window, text=self.translate('btn_reset_settings'), command=self.reset_to_defaults,
            bg="#444444", fg="#ff6666", font=("Segoe UI", 9, "bold"), relief="flat", cursor="hand2", pady=4
        )
        btn_reset.pack(pady=12)
        self.ui_elements['btn_reset'] = btn_reset

        lbl_config_path = tk.Label(
            self.settings_window,
            text=f"{self.translate('lbl_config_location')}\n{CONFIG_FILE}",
            bg="#1e1e1e", fg="#555555", font=("Segoe UI", 7), justify="center", wraplength=320
        )
        lbl_config_path.pack(pady=(0, 10))
        self.ui_elements['lbl_config_path'] = lbl_config_path

    def update_language(self, lang):
        self.current_lang = lang
        self.apply_translations()
        self.save_current_config()
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.title(self.translate('settings_title'))

    # ---------- UI helpers ----------

    def ui(self, fn, *args, **kwargs):
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
        folder = filedialog.askdirectory()
        if folder:
            self.input_dir = folder
            display_name = os.path.basename(folder) or folder
            self.lbl_in.config(text=f"📥 {display_name}", fg="#00ffcc")
            self.check_ready()

    def select_output(self):
        folder = filedialog.askdirectory()
        if folder:
            self.output_dir = folder
            display_name = os.path.basename(folder) or folder
            self.lbl_out.config(text=f"📤 {display_name}", fg="#00ffcc")
            self.check_ready()

    def check_ready(self):
        if self.input_dir and self.output_dir:
            self.btn_run.config(state="normal", bg="#1db954", fg="white", cursor="hand2")

    # ---------- ffmpeg helpers ----------

    def get_ffmpeg_path(self):
        return get_resource_path(os.path.join("bin", "ffmpeg.exe"))

    def get_ffprobe_path(self):
        return get_resource_path(os.path.join("bin", "ffprobe.exe"))

    def get_audio_info(self, filepath):
        ffprobe_path = self.get_ffprobe_path()
        if not os.path.exists(ffprobe_path):
            return ""
        try:
            creationflags = 0x08000000 if os.name == 'nt' else 0
            cmd = [
                ffprobe_path, "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=sample_rate,bit_rate,codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1", filepath
            ]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags,
                timeout=FFPROBE_TIMEOUT_SEC
            )
            lines = res.stdout.decode().strip().splitlines()
            if len(lines) >= 3:
                codec, sr, br = lines[0].upper(), lines[1], lines[2]
                br_kbps = f"{int(br)//1000}kbps" if br.isdigit() else ""
                return f"ℹ️ Spec: {codec} | {sr}Hz | {br_kbps}"
        except Exception:
            pass
        return ""

    def check_ffmpeg_available(self):
        ffmpeg_path = self.get_ffmpeg_path()
        if not os.path.exists(ffmpeg_path):
            messagebox.showerror(
                self.translate('msg_ffmpeg_err_title'),
                f"{self.translate('msg_ffmpeg_err_body')}{ffmpeg_path}"
            )
            return False
        return True

    def get_duration(self, filepath):
        ffprobe_path = self.get_ffprobe_path()
        if not os.path.exists(ffprobe_path):
            return None
        try:
            creationflags = 0x08000000 if os.name == 'nt' else 0
            result = subprocess.run(
                [ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags,
                timeout=FFPROBE_TIMEOUT_SEC
            )
            return round(float(result.stdout.decode().strip()), 2)
        except Exception:
            return None

    def build_audio_filters(self):
        sens_key = self.config.get("sensitivity", "sensitivity_normal")
        threshold = SENSITIVITY_MAP.get(sens_key, "-40dB")
        return (
            f"silenceremove=start_periods=1:start_duration=0.5:start_threshold={threshold},"
            "areverse,"
            f"silenceremove=start_periods=1:start_duration=0.5:start_threshold={threshold},"
            "areverse,"
            "loudnorm=I=-16:TP=-1.5:LRA=11"
        )

    def process_one_file(self, in_file, out_file):
        ffmpeg_path = self.get_ffmpeg_path()
        selected_fmt = self.export_format_var.get().lower()
        
        out_base = os.path.splitext(out_file)[0]
        actual_out_file = f"{out_base}.{selected_fmt}"

        sr_val = self.export_samplerate_var.get()
        ar_param = []
        if "44100" in sr_val:
            ar_param = ["-ar", "44100"]
        elif "48000" in sr_val:
            ar_param = ["-ar", "48000"]

        bitrate_param = []
        if selected_fmt in ["mp3", "m4a", "aac", "ogg"]:
            bitrate_param = ["-b:a", self.export_bitrate_var.get()]

        cmd = [
            ffmpeg_path, "-y", "-i", in_file,
            "-af", self.build_audio_filters(),
            *ar_param,
            *bitrate_param,
            "-map_metadata", "0",
            actual_out_file
        ]

        creationflags = 0x08000000 if os.name == 'nt' else 0
        try:
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags,
                timeout=FFMPEG_TIMEOUT_SEC
            )
        except subprocess.TimeoutExpired:
            # Το ffmpeg κόλλησε (π.χ. κατεστραμμένο αρχείο) -- αντί να κρεμάσει
            # για πάντα την επεξεργασία, το αντιμετωπίζουμε σαν αποτυχία αυτού
            # του αρχείου και συνεχίζουμε κανονικά με τα υπόλοιπα.
            timeout_msg = f"Timeout: το ffmpeg δεν απάντησε μέσα σε {FFMPEG_TIMEOUT_SEC}s.".encode("utf-8")
            result = subprocess.CompletedProcess(cmd, returncode=-1, stdout=b"", stderr=timeout_msg)
        return result, actual_out_file

    # ---------- Preview Single File ----------

    def run_preview(self, filepath=None):
        if self.processing:
            return
        if not self.check_ffmpeg_available():
            return
            
        if not filepath:
            supported_types = "*.mp3;*.wav;*.flac;*.m4a;*.aac;*.ogg"
            filepath = filedialog.askopenfilename(
                filetypes=[("Audio files", supported_types), ("All files", "*.*")]
            )
        if not filepath:
            return

        info_text = self.get_audio_info(filepath)
        self.lbl_file_info.config(text=info_text)

        self.btn_preview.config(state="disabled", bg="#333333")
        self.lbl_status.config(text="Preview...")
        threading.Thread(target=self._preview_worker, args=(filepath,), daemon=True).start()

    def _preview_worker(self, filepath):
        # Ελευθέρωση του Pygame player για να μην κλειδώνει το αρχείο
        self.stop_audio()
        if HAS_PYGAME:
            try:
                pygame.mixer.music.unload()
            except Exception:
                pass

        name, _ = os.path.splitext(os.path.basename(filepath))
        selected_fmt = self.export_format_var.get().lower()
        temp_dir = tempfile.gettempdir()
        preview_out = os.path.join(temp_dir, f"{name}_preview.{selected_fmt}")

        orig_duration = self.get_duration(filepath)
        result, actual_out = self.process_one_file(filepath, preview_out)
        new_duration = self.get_duration(actual_out) if result.returncode == 0 else None

        def _done():
            self.btn_preview.config(state="normal", bg="#1f538d")
            self.lbl_status.config(text=self.translate('lbl_status_ready'))
            if result.returncode == 0:
                self.last_preview_orig = filepath
                self.last_preview_trimmed = actual_out
                self.btn_play_orig.config(state="normal", bg="#0f523b")
                self.btn_play_trimmed.config(state="normal", bg="#14356b")

                msg = self.translate('msg_preview_done_body')
                if orig_duration and new_duration:
                    msg += f"\n\nSecs Before: {orig_duration}s\nSecs After: {new_duration}s"
                messagebox.showinfo(self.translate('msg_preview_done_title'), msg)
            else:
                err = result.stderr.decode(errors="ignore")[-500:]
                messagebox.showerror(self.translate('msg_preview_err_title'), f"{self.translate('msg_preview_err_body')}\n\n{err}")
        self.ui(_done)

    # ---------- Batch Processing ----------

    def request_cancel(self):
        self.cancel_requested = True
        self.lbl_status.config(text="Cancelling...")

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
        valid_exts = ('.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg')
        all_files = [f for f in os.listdir(self.input_dir) if f.lower().endswith(valid_exts)]
        
        if not all_files:
            self.ui(
                messagebox.showwarning,
                self.translate('msg_no_files_title'),
                self.translate('msg_no_files_body')
            )
            self.reset_ui()
            return

        os.makedirs(self.output_dir, exist_ok=True)

        skip_existing = self.skip_existing_var.get()
        fmt_ext = self.export_format_var.get().lower()

        files_to_process = []
        skipped = []
        for f in all_files:
            base_name, _ = os.path.splitext(f)
            out_filename = f"{base_name}.{fmt_ext}"
            out_path = os.path.join(self.output_dir, out_filename)
            if skip_existing and os.path.exists(out_path):
                skipped.append(f)
            else:
                files_to_process.append(f)

        if skipped:
            self.log(f"⏭ Skipped {len(skipped)} existing files.")

        if not files_to_process and skipped:
            # Όλα τα αρχεία υπήρχαν ήδη στον προορισμό -- ξεκάθαρο μήνυμα
            # αντί για ένα μπερδεμένο "0/0 επιτυχή".
            self.log(f"✅ {self.translate('msg_all_skipped_body')} {len(skipped)}")
            self.ui(
                messagebox.showinfo,
                self.translate('msg_all_skipped_title'),
                f"{self.translate('msg_all_skipped_body')} {len(skipped)}"
            )
            self.reset_ui()
            return

        total_files = len(files_to_process)
        self.ui(self.progress.config, maximum=max(total_files, 1), value=0)

        log_rows = []
        success_count = 0
        file_times = []

        for i, filename in enumerate(files_to_process):
            if self.cancel_requested:
                self.log("⛔ Cancelled by user.")
                break

            self.ui(self.lbl_status.config, text=f"Processing ({i+1}/{total_files}): {filename}")
            start_time = time.time()

            in_file = os.path.join(self.input_dir, filename)
            out_file = os.path.join(self.output_dir, filename)

            orig_duration = self.get_duration(in_file)
            result, actual_out = self.process_one_file(in_file, out_file)
            elapsed = time.time() - start_time
            file_times.append(elapsed)

            if result.returncode == 0:
                success_count += 1
                new_duration = self.get_duration(actual_out)
                status = "OK"
                error_msg = ""
                self.log(f"✅ {filename} ({elapsed:.1f}s)")
            else:
                new_duration = None
                status = "ERROR"
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

            avg_time = sum(file_times) / len(file_times)
            remaining = total_files - (i + 1)
            eta_sec = int(avg_time * remaining)
            eta_text = f"ETA: ~{eta_sec}s" if remaining > 0 else ""
            self.ui(self.lbl_eta.config, text=eta_text)

        log_path = os.path.join(self.output_dir, "processing_log.csv")
        try:
            with open(log_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "filename", "status", "original_duration_sec", "new_duration_sec", "error"
                ])
                writer.writeheader()
                writer.writerows(log_rows)
            self.log(f"📄 Log saved: {log_path}")
        except Exception as e:
            self.log(f"⚠ Failed to save log: {e}")

        processed = len(log_rows)

        summary = (
            f"{self.translate('msg_completed_body_success')} {success_count}/{processed}\n"
            f"{self.translate('msg_completed_body_skipped')} {len(skipped)}\n"
            f"{self.translate('msg_completed_body_out')}\n{self.output_dir}\n\n"
            f"Log: processing_log.csv"
        )

        self.play_finish_sound()
        title_key = 'msg_cancelled_title' if self.cancel_requested else 'msg_completed_title'
        self.ui(messagebox.showinfo, self.translate(title_key), summary)
        self.reset_ui()

    def reset_ui(self):
        self.processing = False
        self.cancel_requested = False

        def _reset():
            self.btn_in.config(state="normal")
            self.btn_out.config(state="normal")
            self.btn_preview.config(state="normal", bg="#1f538d")
            self.btn_run.config(state="normal" if self.input_dir and self.output_dir else "disabled", bg="#1db954" if self.input_dir and self.output_dir else "#444444")
            self.btn_cancel.config(state="disabled")
            self.progress["value"] = 0
            self.lbl_eta.config(text="")
            self.lbl_status.config(text=self.translate('lbl_status_ready'))
        self.ui(_reset)


# --- Single Instance Lock -------------------------------------------------
# Εμποδίζει να ανοίγει νέο παράθυρο κάθε φορά που πατιέται το εικονίδιο ενώ
# η εφαρμογή είναι ήδη ανοιχτή. Βασίζεται σε ένα τοπικό TCP socket σαν
# "κλειδαριά" (bind σε σταθερή θύρα). ΔΕΝ βάζουμε SO_REUSEADDR: σε Windows
# αυτό επιτρέπει σε μια 2η διεργασία να κάνει bind() στην ΙΔΙΑ θύρα ενώ μια
# 1η ήδη "ακούει" πάνω της, ακυρώνοντας όλη τη λογική του κλειδώματος.
SINGLE_INSTANCE_PORT = 47661

def acquire_single_instance_lock():
    """
    (is_primary, sock)
      True, <socket>  -> είμαστε η μοναδική διεργασία, κράτα το socket ζωντανό
                          όσο τρέχει η εφαρμογή.
      False, None     -> υπάρχει ήδη ανοιχτή εφαρμογή, μην ανοίξεις παράθυρο.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            except OSError:
                pass
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
        s.listen(5)
        return True, s
    except OSError:
        try:
            client = socket.create_connection(("127.0.0.1", SINGLE_INSTANCE_PORT), timeout=1.5)
            client.sendall(b"SHOW")
            client.close()
            return False, None
        except Exception:
            return True, None
    except Exception:
        return True, None


def _start_single_instance_listener(lock_sock, root):
    """Ακούει για μηνύματα "SHOW" από επόμενες εκκινήσεις και φέρνει το
    υπάρχον παράθυρο μπροστά."""
    def _bring_to_front():
        root.deiconify()
        root.lift()
        root.focus_force()

    def _listen():
        while True:
            try:
                conn, _ = lock_sock.accept()
            except OSError:
                break
            try:
                data = conn.recv(16)
                if data == b"SHOW":
                    root.after(0, _bring_to_front)
            except Exception:
                pass
            finally:
                conn.close()

    threading.Thread(target=_listen, daemon=True).start()


if __name__ == "__main__":
    is_primary, lock_sock = acquire_single_instance_lock()
    if not is_primary:
        sys.exit(0)

    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    if lock_sock is not None:
        _start_single_instance_listener(lock_sock, root)
    app = MP3ProcessorApp(root)
    root.mainloop()