"""Build hindu_names_dict v2 from v1 + curated surname expansion + DB-derived stats.

Why: the scraper's classifier (src/community_pipeline.py) matched ANY name
token against the dict, so western middles like `MURPHY JAY A` or
`TURNER DOUGLAS RAY` flagged as hindu — 50% of is_hindu rows were false
positives (measured 2026-10-16 over 43,824 flagged rows). v2 switches the
classifier to position-aware matching:

  - surname-position hit (first token of any owner segment) -> hindu
  - AMBIGUOUS surnames (also common English: RAY, PAUL, GILL...) need a
    distinctive Indian given-name corroborator
  - given-position hit counts only for DISTINCTIVE givens (PIYUSH, ANIL...)
    so `KILBOURNE RAY A` dies but `GULLAPALLI PRASAD` (dict-missing
    surname) survives

This script owns two data-derived sets:
  distinctive_first_names — hindu_first tokens whose given-position
    occurrences co-occur with an Indian-surname-position hit >= PREC_MIN
    of the time (measured against the properties table, so the threshold
    runs AFTER the surname expansion lands in the dict)
  ambiguous_last_names    — surname-position tokens that also appear as
    common English surnames; curated list below, data-checked

Run: ./venv/bin/python scripts/build_hindu_dict_v2.py [--dry-run]
Writes data/hindu_names_dict.json in place (v1 fields preserved).
"""
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DICT_PATH = "data/hindu_names_dict.json"
DB_PATH = "data/property_search.db"

PREC_MIN = 0.55   # given-token corroboration rate to count as distinctive
MIN_OCCURRENCES = 25

# Curated expansion: common Indian surnames missing from v1, by community.
# Kept conservative — pan-Indian diaspora in MD skews Telugu/Tamil/Gujarati/
# Punjabi/Bengali/Nepali. Homographs that are ALSO common English surnames
# (RAY, DEAN, PAUL...) go in AMBIGUOUS below instead.
CURATED_SURNAMES = """
SHARMA VERMA GUPTA YADAV MISHRA PANDEY SHUKLA TRIPATHI DUBEY CHAUBEY TIWARI
SRIVASTAVA AWASTHI AGNIHOTRI DIXIT DWIVEDI UPADHYAY PATHAK SINHA THAKUR
CHAUHAN RATHORE PARIHAR TOMAR SOLANKI PARMAR KASHYAP VASHISHT JOSHI PANT
BISHT NEGI RAWAT UNIYAL SEMWAL MEHRA CHANANA GOEL GOYAL BANSAL MIGLANI
PATEL SHAH MEHTA DESAI GANDHI PARIKH MODI THAKKAR RATHOD MAKWANA VAGHELA
CHAVDA JADEJA PANDYA BAXI KOTAK MASRANI KESWANI SANGHVI DOSHI TRIVEDI
CHOKSHI BHAVSAR VORA BHATT ADVALPALLA ARORA KAPOOR MALHOTRA CHOPRA BHALLA
SETHI TANDON BAJAJ BEDI DHILLON SIDHU SANDHU RANDHAWA GREWAL SAHOTA TOOR
SODHI KOHLI CHADHA VIJ RAINA KAUL WADHWA NAGPAL LUTHRA KHURANA TREHAN BERI
MARWAH AHUJA SOOD SACHDEVA SAMRA BAINS BHANGOO CHHINA DULA
DESHPANDE KULKARNI PATIL KHOT GOKHALE PARANJAPE BHAVE SATHE LELE RANADE
CHATURVEDI DUBE BAJPAI MISHRA SAXENA TRIVEDI MATHUR RASTOGI AGRAWAL
MUKHERJEE CHATTERJEE SEN DAS BOSE GANGULY GHOSH LAHIRI SENGUPTA DUTTA
DATTA ROY PAL DEY KAR CHAKRABORTY MAJUMDAR MUKHOPADHYAY SAHA DASGUPTA
SARKAR SAMANTA BANERJI CHAUDHURI DUTT PALIT MAITRA BHADRA
REDDY NAIDU RAJU RAO MURTHY MOORTHY SHASTRY SASTRY IYENGAR CHOWDARY
KANUMURI GAVINI VARMA CHERUKURI VEMURI KATTA GANTLA KANDULA GUDIPATI
NUNNA KARAMSETTY NARAHARI VEMAGIRI MALLELA PATHIREDDY SURAPUREDDY
VANAPARTHI BATHINI CHINTAKINDI KOMMIREDDY GULLAPALLI PATTANAYAK
AYYAGARI BOLLAVARAM THOKALA SANNIDDI KANDREGULA MADIRAJU PARUCHURI
POTTIPREDDY REDDAPPA GOUD KAMMA BALABHADRAPATRUNI
IYER PILLAI CHETTIAR NAGARAJAN SUBRAMANIAN SUNDARAM KRISHNAMURTHY
RAMANATHAN VENKATARAMAN SUNDARESAN RAJAGOPALAN SRINIVASAN VENKATESAN
PARTHASARATHY GANESAN MUTHUSWAMY ANNAMALAI MARIMUTHU PERIASAMY
CHINNASAMY MUDALIAR PALANISAMY BALASUBRAMANIAM LAKSHMANAN GOPALAKRISHNAN
RAMACHANDRAN CHIDAMBARAM VISWANATHAN MEENAKSHISUNDARAM PANCHANATHAM
HEGDE SHETTY SHENOY NAYAK NADIG KAMATH PRABHU BHANDARY RAO
NAIR MENON PANICKER KURUP NAMBIAR VARIER WARRIER MARAR THAMPI
POOTHURU VYDHIA
ADHIKARI BHATTARAI BASNET GURUNG MAGAR SHRESTHA TAMANG LIMBU THAPA KARKI
OLI DAHAL NEUPANE POUDEL BHANDARI RIMAL SITAULA GHIMIRE DHAKAL PANT
GAUTAM BASKOTA JOSHI UPRETY ADHIKARI POKHAREL RIJAL ACHARYA
ADVANI HINGORANI KATRAK TOLANI HIRANANDANI LALWANI MURJANI WADHANI
DAR FOTEDAR KASHYAP
SEHGAL SAREEN ANAND CHAUDHRY GIRI GAUR PRAKASH SACHAN TEWARI
KARUNAKARAN DODDANNA CHUMBLE CHODAY GOPALAN NATARAJ NUNE BHAMIDIPATI
KADAMBI DOSAJ KONANKI PARIBRAMATH CHERIYAN THANGASAMY NICHANI GANJI
ESWARAN BEHERA SUD RAO SASTRY SURYA
JHA MAHTO BHATTARAI BARAL LAMA SHERPA RAI
THAKURTA MOHANTY PANDA MISHRA ROUT DASH PANDA
"""

# Surname-position tokens that are ALSO common English surnames or show up
# as western given-first names in SDAT rows. A hit on one of these alone is
# NOT sufficient — needs a distinctive given token. Keep tight: real
# homographs only (PRASAD/PAL/DEY/BABU are NOT English surnames — they stay
# unambiguous; BALASUBRAMANIAN/CHOWDHURY/RANDHAWA are unambiguously Indian).
# v2.1 additions measured from post-reclass flag audit 2026-10-16:
# JAY/MILAN/ROHAN/RUBY/NEEL = English given-first or Irish; DEEP/SAI/AMAN/
# AMAR/HARSH/KAMAL/SUHAS = Indian givens misparsed at surname position;
# DAR = mixed Kashmiri/Arabic; DUBE = African (Zulu) vs Indian DUBEY.
AMBIGUOUS_SURNAMES = """
RAY PAUL ROY SEN DEAN THOMAS JOHN GEORGE MATHEW MARK LUKE MAN MIN MARTIN
GILL DEV MANN
JAY MILAN ROHAN RUBY NEEL DEEP SAI AMAN AMAR HARSH KAMAL SUHAS DAR DUBE
"""

# v1 sikh_names carried 'KA' and 'SI' — not names, they matched thousands of
# western middle initials/abbreviations ("... KA", "...SI"). Dropped here.
CURATED_SIKH = """
SINGH KAUR BAJWA DHILLON SIDHU SANDHU BRAR GREWAL NIJJAR THIND PUREWAL
DHALIWAL CHAHAL GILL MANN SODHI TOOR SAHOTA BHANGOO BASI GILL
"""

# Extra distinctive given names always counted, regardless of DB stats
# (rare-but-unambiguous Indian givens the stats threshold may miss).
CURATED_DISTINCTIVE_GIVENS = """
PIYUSH NIRAJ ANURAG PAWAN SHRUTI VIKAS VIMAL SAHIL GOVIND YASH SUNIL
MAHESH PRASAD ANIL KUMAR RAJESH SURESH RAMESH MAHESH ANAND DEEPAK
ASHOK VIJAY AJAY SANJAY RAJIV NITIN ROHIT AMIT ATUL PANKAJ MANOJ
KIRTI PRAKASH VENKAT RAMA LAKSHMI PRIYA DEEPIKA ANJALI SNEHA POOJA
NISHA MEENA GEETA SEETA VANDANA SHOBHA LATA USHA PUSHPA
SHASHANK JATIN HEMANT GAURAV CHIRAG ALOK HARSH PRATIK PRANAV NEERAJ
ASHWANI BHASKAR KUNAL
"""


def segments(name: str):
    """Split a raw SDAT owner string into owner segments (token lists).

    SDAT format is SURNAME FIRST MIDDLE; co-owners are joined by & / AND.
    """
    segs = []
    for seg in re.split(r"\b(?:&|AND)\b", name.upper()):
        toks = [t for t in re.sub(r"[^A-Z\s]", " ", seg).split()
                if len(t) > 1 and t.isalpha()]
        if toks:
            segs.append(toks)
    return segs


def main():
    dry = "--dry-run" in sys.argv
    with open(DICT_PATH) as f:
        d = json.load(f)

    def words(block):
        return sorted({w for w in block.split() if len(w) > 1})

    v1_last = set(d.get("hindu_last_names", []))
    v1_first = set(d.get("hindu_first_names", []))

    new_last = v1_last | set(words(CURATED_SURNAMES))
    # ambiguous set only matters for tokens actually in the surname pool
    sikh = {w for w in words(CURATED_SIKH) if w not in ("KA", "SI")}
    ambiguous = (set(words(AMBIGUOUS_SURNAMES)) & (new_last | v1_first | sikh)) | {"RAY", "PAUL"}
    d["ambiguous_last_names"] = sorted(ambiguous)
    d["hindu_last_names"] = sorted(new_last)
    d["sikh_names"] = sorted(sikh)
    # SDAT business/junk strings that reach the classifier ("DEEP BLUE 2956
    # CORP", "SAI LAUREL HOUSING") — more markers kill them at _clean_name
    d["business_markers"] = sorted(set(d.get("business_markers", [])) | {
        "HOUSING", "APARTMENTS", "RENTALS", "HOMES", "COMPANY", "COMPANIES",
        "ENTERPRISES", "VENTURES", "GROUP", "BUILDERS", "DEVELOPMENT",
    })

    # --- DB-derived: given-token corroboration with the EXPANDED surname set
    sur = new_last | set(d["sikh_names"]) | (v1_first - ambiguous)
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    rows = con.cursor().execute(
        "SELECT owner_name FROM properties WHERE owner_name IS NOT NULL"
    ).fetchall()
    con.close()

    stats = defaultdict(lambda: [0, 0])  # token -> [with surname hit, without]
    for (name,) in rows:
        segs = segments(name)
        if not segs:
            continue
        sur_hit = any(s[0] in sur for s in segs)
        for s in segs:
            for t in s[1:]:
                if t in v1_first:
                    stats[t][0 if sur_hit else 1] += 1

    distinctive = set(words(CURATED_DISTINCTIVE_GIVENS))
    report = []
    for t, (w, wo) in stats.items():
        total = w + wo
        if total >= MIN_OCCURRENCES and w / total >= PREC_MIN:
            distinctive.add(t)
        if total >= 20:
            report.append((w / total, total, t))
    d["distinctive_first_names"] = sorted(distinctive & (v1_first | set(words(CURATED_DISTINCTIVE_GIVENS))))

    print(f"surnames: {len(v1_last)} -> {len(new_last)}")
    print(f"ambiguous: {len(ambiguous)} -> {d['ambiguous_last_names']}")
    print(f"distinctive givens: {len(d['distinctive_first_names'])}")
    print("\ngiven tokens by corroboration (precision, occurrences):")
    for prec, total, t in sorted(report, reverse=True)[:20]:
        mark = "+" if t in distinctive else " "
        print(f"  {mark} {t:15s} {prec:.2f}  n={total}")

    if not dry:
        with open(DICT_PATH, "w") as f:
            json.dump(d, f, indent=1)
        print(f"\nwrote {DICT_PATH}")


if __name__ == "__main__":
    main()
