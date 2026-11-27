# Accuracy and speed of the strip's Copy text (agent-panel-shots.py: Ocr.read()) on synthetic terminal screenshots.
# Renders Claude Code style answers (a heading, a paragraph, a Turkish numbered list 1 to 19 under short headings,
# wrapped items, quotes, p=none, X'te) in Consolas on Windows Terminal's light colours, with known text, then reads
# them with the committed version (git HEAD) and with the working copy, and prints character accuracy (1 - edit
# distance / length) and line accuracy (lines of the known text found, in order) for each.
# Run: python tools/test-ocr.py [--unit] [--keep] [--only NAME] (--unit: only the layout checks on hand-made word
# boxes, no OCR). Touches no clipboard and no window; the pictures are written
# to a temporary folder and deleted at the end (--keep leaves them and prints where).
import difflib, importlib.util, os, shutil, subprocess, sys, tempfile, time
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
BG, FG, BLUE = (250, 250, 250), (56, 58, 66), (1, 132, 188)  # One Half Light, Windows Terminal's light scheme

# (kind, text): head = bold line, para = paragraph, item = numbered item, sub = group heading, "" = blank line.
# `code` is drawn in blue without its backticks, as Claude Code does.
ANSWER = [
    ("head", "● Önerilen düzenleme planı"),
    ("", ""),
    ("para", "Aşağıdaki plan, sitenin e-posta ve ödeme ayarlarını sırayla düzeltir. Her grubu bitirdikten sonra "
             "kısa bir kontrol yapın; sorun çıkarsa bir önceki adıma dönün ve nedenini not edin, çünkü aynı hata "
             "ikinci kez daha zor bulunur."),
    ("", ""),
    ("sub", "Hazırlık"),
    ("item", "Depoyu güncelleyin ve yeni bir dal açın."),
    ("item", "DNS kayıtlarının yedeğini alın (TXT, MX ve CNAME)."),
    ("item", "Alan adının DMARC kaydında `p=none` değerinin olduğunu doğrulayın; bu değer raporları toplar ama hiçbir "
             "iletiyi engellemez, yani şu an kimse korunmuyor ve sahte gönderenler rahatça geçebiliyor."),
    ("item", "Gönderen adresini \"Destek Ekibi\" olarak ayarlayın."),
    ("", ""),
    ("sub", "E-posta"),
    ("item", "SPF kaydına yalnızca kullandığınız servisleri ekleyin, gereksiz include satırlarını silin."),
    ("item", "DKIM anahtarını 2048 bit olarak yeniden üretin."),
    ("item", "Test iletisini Gmail'e ve Outlook'a gönderin, başlıkları karşılaştırın ve imzanın geçtiğini görün; "
             "geçmiyorsa anahtarın DNS'e doğru yazıldığını kontrol edin."),
    ("item", "Raporlar iki hafta temiz gelirse `p=quarantine` değerine geçin."),
    ("item", "Bounce adresini ayrı bir kutuya yönlendirin."),
    ("", ""),
    ("sub", "Okuma ücretleri"),
    ("item", "X'te her okuma için ücret alınır; bu yüzden aynı gönderiyi tekrar tekrar çekmek yerine sonucu "
             "önbellekte tutun, günlük sınırı aşmayın ve sayacı her sabah sıfırlayın, yoksa ay sonunda fatura "
             "beklenenden yüksek çıkar."),
    ("item", "X'in API anahtarını yalnızca sunucuda saklayın."),
    ("item", "X'ten gelen yanıtta hata kodu 429 ise beklemeyi iki katına çıkarın."),
    ("item", "Aylık faturayı ilk hafta içinde kontrol edin."),
    ("", ""),
    ("sub", "Yayın"),
    ("item", "Değişiklikleri önce test ortamında deneyin."),
    ("item", "Sürüm notunu Türkçe ve İngilizce yazın: \"What changed\" ve \"Neler değişti\" başlıklarıyla, her "
             "maddeye bir örnek ekleyerek."),
    ("item", "Kullanıcılara giden e-postada yeni gönderen adresini ve destek bağlantısını açıkça belirtin, eski "
             "adresi bir ay daha açık tutun ve gelen yanıtları yeni kutuya yönlendirin."),
    ("item", "Yayından sonra hata günlüklerini 24 saat izleyin."),
    ("item", "Sorun çıkarsa önceki sürüme dönün, nedenini kısa bir notla kaydedin ve düzeltmeyi ayrı bir dalda "
             "hazırlayıp yeniden test edin; aynı gün ikinci kez yayınlamayın."),
    ("item", "Bitti mi? Evet: 19 adımın hepsi tamam."),
]

# name: (width, height, font px, face): full-window screenshots at 125 % and 100 % scale, a half-screen and a narrow
# window, and one in a proportional face (Segoe UI), which is not a terminal but must not come out worse
PICTURES = {"full-125": (1920, 1080, 20, "consola"), "full-100": (1920, 1080, 16, "consola"),
            "half-18": (1280, 1040, 18, "consola"), "narrow-16": (960, 1040, 16, "consola"),
            "small-14": (1200, 1000, 14, "consola"), "full-150": (2880, 1620, 24, "consola"),
            "segoe-15": (1100, 1200, 15, "segoeui")}


def truth():
    """The text Copy should give: one line per logical line, wrapped items whole, blank lines kept."""
    out, n = [], 0
    for kind, text in ANSWER:
        if kind == "item":
            n += 1
            text = "%d. %s" % (n, text)
        out.append(text.replace("`", ""))
    return out


def wrap(text, room, size):
    """Word wrap as a terminal renderer does: a word goes down when it does not fit in the room left."""
    lines, cur = [], ""
    for w in text.split(" "):
        if cur and size(cur + " " + w) > room:
            lines.append(cur)
            cur = w
        else:
            cur = cur + " " + w if cur else w
    return lines + [cur]


def render(path, width, height, px, face):
    """A terminal in Consolas (every character one cell) or, as a check that nothing assumes a fixed pitch, the
    same answer in a proportional face laid out the same way."""
    mono = face == "consola"
    reg = ImageFont.truetype(os.path.join(FONTS, face + ".ttf"), px)
    bold = ImageFont.truetype(os.path.join(FONTS, ("consolab" if mono else face + "b") + ".ttf"), px)
    cw = reg.getlength("M")
    size = (lambda t: len(t.replace("`", "")) * cw) if mono else (lambda t: reg.getlength(t.replace("`", "")))
    a, d = reg.getmetrics()
    lh = a + d if mono else int(1.4 * px)
    pad = 8
    room = width - 2 * pad - cw
    img = Image.new("RGB", (width, height), BG)
    dr = ImageDraw.Draw(img)
    rows, n, two = [], 0, size("  ")  # each row: (indent px, text, bold)
    for kind, text in ANSWER:
        if not kind:
            rows.append((0, "", False))
            continue
        if kind == "item":
            n += 1
            mark = "%d. " % n
            body = wrap(text, room - two - size(mark), size)
            rows.append((two, mark + body[0], False))
            rows += [(two + size(mark), t, False) for t in body[1:]]
        else:
            ind = 0 if kind == "head" else two
            rows += [(ind, t, kind in ("head", "sub")) for t in wrap(text, room - ind, size)]
    y = pad
    for ind, text, b in rows:
        x, code = pad + ind, False
        for part in text.split("`"):
            f = bold if b else reg
            for ch in part if mono else [part]:
                dr.text((x, y), ch, font=f, fill=BLUE if code else FG)
                x += cw if mono else f.getlength(ch)
            code = not code
        y += lh
    assert y < height, "answer does not fit in %s" % path
    img.save(path)


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def score(gt, out):
    g, o = "\n".join(gt), "\n".join(out)
    chars = 1 - distance(g, o) / len(g)
    m = difflib.SequenceMatcher(None, gt, out, autojunk=False)
    lines = sum(b.size for b in m.get_matching_blocks()) / len(gt)
    return chars, lines


def boxes(rows, cw=10, lh=20, h=14):
    """OCR words as a fixed-pitch terminal would give them: rows of (column, text), a row None for a blank one;
    a word of punctuation alone is short and sits low, as Windows' OCR boxes it."""
    out = []
    for n, r in enumerate(rows):
        if r is None:
            continue
        col, text = r
        for w in text.split(" "):
            if w:
                low = all(c in ",.;:" for c in w)
                out.append((col * cw + 1, n * lh + (h - 4 if low else 0) + (n % 3) * 0.7, len(w) * cw - 2,
                            4 if low else h, w))
            col += len(w) + 1
    return out


def unit(shots):
    """ocr_rows() on hand-made word boxes: no OCR, no picture."""
    rows = shots.ocr_rows
    assert rows([]) == []
    # a short picture whose rows share a left edge: its longest row is not a wrap
    two = [(0, "Settings and Options"), (0, "Güncel İşlem Listesi")]
    assert rows(boxes(two)) == ["Settings and Options", "Güncel İşlem Listesi"]
    # a list's number and its text are one row, even a little apart in height
    w = boxes([(2, "14. Değişiklikleri önce deneyin.")])
    w[0] = w[0][:1] + (w[0][1] + 3,) + w[0][2:]
    assert rows(w) == ["14. Değişiklikleri önce deneyin."]
    # wrapped items joined, a new item and a blank row kept; the room test: "uzun" fits at col 38, so no join
    wrapped = [(0, "Giriş"), None, (2, "1. bir iki üç dört beş altı yedi sekiz dokuz"), (5, "on onbir"),
               (2, "2. bir iki üç dört beş altı yedi sekiz dokuz"), (5, "on onbir"),
               (2, "3. bir iki üç dört beş altı yedi sekiz dokuz"), (5, "on onbir"),
               (2, "4. bir iki üç dört beş altı yedi sekiz"), (5, "uzun")]
    assert rows(boxes(wrapped)) == ["Giriş", "", "1. bir iki üç dört beş altı yedi sekiz dokuz on onbir",
                                    "2. bir iki üç dört beş altı yedi sekiz dokuz on onbir",
                                    "3. bir iki üç dört beş altı yedi sekiz dokuz on onbir",
                                    "4. bir iki üç dört beş altı yedi sekiz", "uzun"], rows(boxes(wrapped))
    # touching boxes take no space: X'te split by the OCR, a comma boxed alone
    w = [(1, 0, 8, 14, "X"), (13, 0, 18, 14, "'te"), (41, 0, 48, 14, "okuma"), (92, 10, 4, 4, ","),
         (111, 0, 28, 14, "ama")]
    assert rows(w) == ["X'te okuma, ama"], rows(w)
    # "14 ," is "14." on a picture with a numbered list, and left alone without one
    assert rows(boxes([(2, "13. a b"), (2, "14 , c d")])) == ["13. a b", "14. c d"]
    assert rows(boxes([(0, "3, 4 ve 5")])) == ["3, 4 ve 5"]
    print("ocr_rows: 7 checks passed")


def main():
    keep = "--keep" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    tmp = tempfile.mkdtemp(prefix="claude-plus-test-ocr-")
    gt = truth()
    procs = []
    try:
        old = os.path.join(tmp, "shots_head.py")
        with open(old, "wb") as f:
            f.write(subprocess.check_output(["git", "-C", ROOT, "show", "HEAD:agent-panel-shots.py"]))
        before, after = load("shots_before", old), load("shots_after", os.path.join(ROOT, "agent-panel-shots.py"))
        unit(after)
        if "--unit" in sys.argv:
            return
        pics = {}
        for name, (w, h, px, face) in PICTURES.items():
            if only and name != only:
                continue
            pics[name] = os.path.join(tmp, "%s.png" % name)
            render(pics[name], w, h, px, face)
        configs, ps = [("before", before, None)], after.OCR_PS
        variants = os.environ.get("OCR_VARIANTS", "")
        for v in [x for x in variants.split(",") if x] or ["%s:%d" % (after.OCR_LANG, after.OCR_UP)]:
            configs.append(("after " + v, after, v))
        for label, mod, v in configs:
            if v:  # lang:times[:interpolation], e.g. tr:3:Fant
                f = v.split(":")
                mod.OCR_LANG, mod.OCR_UP = f[0], int(f[1])
                mod.OCR_PS = ps.replace("Mode]::Cubic", "Mode]::" + (f[2] if len(f) > 2 else "Cubic"))
            ocr = mod.Ocr()
            procs.append(ocr)
            t0 = time.time()
            ocr._ensure()
            start = time.time() - t0
            tot_c = tot_l = 0
            for name, p in pics.items():
                t0 = time.time()
                out = ocr.read(p)
                first = time.time() - t0
                again = []
                for _ in range(5):
                    t0 = time.time()
                    ocr.read(p)
                    again.append(time.time() - t0)
                again = sorted(again)[2]
                os.replace(p, p + ".moved")  # the keeper moves a pasted screenshot: the worker must not hold it
                os.replace(p + ".moved", p)
                c, l = score(gt, out)
                tot_c, tot_l = tot_c + c, tot_l + l
                print("%-14s %-10s chars %5.1f %%  lines %5.1f %%  read %.2f s (then median %.2f s)"
                      % (label, name, 100 * c, 100 * l, first, again))
                if os.environ.get("OCR_SHOW"):
                    for a in difflib.unified_diff(gt, out, "truth", label, lineterm="", n=0):
                        print("   ", a)
            print("%-14s %-10s chars %5.1f %%  lines %5.1f %%  (worker start %.1f s)\n"
                  % (label, "mean", 100 * tot_c / len(pics), 100 * tot_l / len(pics), start))
            ocr.stop()
    finally:
        for o in procs:
            o.stop()
            if o.proc:
                o.proc.wait(10)  # gone before its last picture is deleted
        if keep:
            print("pictures kept in", tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
