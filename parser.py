# parser.py

KEY_MAP = {
    'inşaat': 'project',
    'insaat': 'project',
    'ad': 'first',
    'soyad': 'last',
    'ücret': 'amount',
    'ucret': 'amount',
    'daire no': 'flat_no',
    'kat': 'floor',
    'taksit': 'installment',
    'tarih': 'date'
}

def parse_whatsapp_message(text):
    data = {}
    for line in text.splitlines():
        if ':' not in line: 
            continue
        raw_key, raw_val = line.split(':', 1)
        key = raw_key.strip().lower()
        val = raw_val.strip()
        # normalize key
        norm = KEY_MAP.get(key)
        if norm:
            data[norm] = val
    return data
