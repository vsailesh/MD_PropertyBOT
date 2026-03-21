import json
import os

def enrich_dict():
    path = "data/hindu_names_dict.json"
    with open(path, 'r') as f:
        data = json.load(f)

    # Extensive list of South Indian and general missing names
    new_first = [
        "MADHU", "SUDHA", "BHAVANI", "KALYAN", "KIRAN", "VENKATA", "SAI", "KRISHNAM", "RAJU", "SRI", 
        "RAMA", "CHANDRA", "SEKHAR", "BABU", "SWAMY", "NATH", "DATTA", "BHARATH", "RAGHAVA", "KISHORE", 
        "GOPAL", "HARI", "SHIVA", "PRABHAKAR", "MURALI", "SUDHAKAR", "RAMANA", "RATNAM", "SESHA", 
        "SRINIVASA", "NAGA", "VENKATARAMANA", "PRASAD", "VENU", "GOPALA", "KONDAL", "RANGARAO", 
        "NARASIMHA", "PADMA", "LAKSHMI", "SARASWATHI", "LALITHA", "PARVATHI", "ANURADHA", "SUJATHA", 
        "KAVITHA", "SUNITHA", "RAMANI", "SITA", "SABITHA", "VIJAYA", "BHANUMATHI", "NAGALAKSHMI", 
        "SAILAJA", "SARADA", "KUSUMA", "MALATHI", "PADMINI", "SUCHARITHA", "VARALAKSHMI", "VASANTHA", 
        "NIRMALA", "HEMALATHA", "KUMARI", "SUREKHA", "REVATHI", "SAVITHRI", "VANAJA", "KALPANA", 
        "SOUJANYA", "JAYALAKSHMI", "MEENAKSHI", "RADHIKA", "LAVANYA", "SWAROOPA", "MADHAVI", "SUSHMA",
        "PRAMEELA", "VIMAL", "SUJITHA", "KAMAKSHI", "GAYATHRI", "RAMYA", "SARANYA", "BHANU", "KASTHURI",
        "SREERAM", "GOWTHAM", "KARTHIK", "SARATH", "PRAVEEN", "NAVIN", "NARENDER", "SURENDER", "UPENDER",
        "RAGHAVENDRA", "MAHENDRA", "RAJENDRA", "AMARNATH", "BHADRINATH", "SREEDHAR", "GANGADHAR", "RAJASHEKHAR",
        "PRITHVIRAJ", "DHARANI", "RAMANUJAM", "BALASUBRAMANIAN", "RAMAKRISHNA", "SIVAKUMAR", "VIJAYAKUMAR"
    ]

    new_last = [
        "BELLAM", "YARLAGADDA", "GOGINENI", "MANDAVA", "KANNEGANTI", "AKKINENI", "DAGGUBATI", "KODALI", 
        "CHUKKAPALLI", "CHENNUPATI", "THOTA", "KAMINENI", "VADLAMUDI", "YELAMANCHILI", "VALLABHANENI", 
        "NANDAMURI", "BODEPUDI", "GUTTIKONDA", "KAKANI", "KARUMANCHI", "KOSARAJU", "ALURU", "GADE", 
        "VEMURI", "YENIGALLA", "ATLURI", "VEERAMACHANENI", "MUTHINENI", "CHALASANI", "NUTHAKKI", "PALLA", 
        "POLAVARAPU", "BOLLINENI", "DASARI", "GOTTIPATI", "CHINNAM", "KOMMINENI", "MAKKENI", "SURAAPANENI", 
        "TUMMALA", "KATTA", "YALAMANCHILI", "BOYAPATI", "BOPPANA", "MUTHYALA", "KOTERU", "DANDAMUDI", 
        "KONERU", "LINGA", "PUNUKOLLU", "NIMMAGADDA", "NARRA", "KOLASANI", "DODDA", "GUNTUPALLI", 
        "BELLAMKONDA", "TALLURI", "TALLAPUDI", "MAGANTI", "MACHENENI", "PASUPULETI", "MUVVA", "MUPPA", 
        "KATRAGADDA", "GARA", "YENDRURI", "MEDASANI", "TIRUMALA", "VELAGAPUDI", "CHIGURUPATI", "ANNAMANENI",
        "JAMPALA", "VEGULLA", "TUMATI", "JONNALAGADDA", "NANNAPANENI", "BATTINI", "VUGGUMUDI", "CHIRUMAMILLA"
    ]

    added_first = 0
    for name in new_first:
        if name not in data["hindu_first_names"]:
            data["hindu_first_names"].append(name)
            added_first += 1
            
    added_last = 0
    for name in new_last:
        if name not in data["hindu_last_names"]:
            data["hindu_last_names"].append(name)
            added_last += 1

    # Sort them for clean output
    data["hindu_first_names"].sort()
    data["hindu_last_names"].sort()

    with open(path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Successfully added {added_first} first names and {added_last} last names to {path}!")

if __name__ == "__main__":
    enrich_dict()
