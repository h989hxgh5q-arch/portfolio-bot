from flask import Flask, request
import gspread
from google.oauth2.service_account import Credentials
from twilio.rest import Client
import os, json, re, requests
from datetime import datetime

app = Flask(__name__)

TWILIO_SID = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM = os.environ.get('TWILIO_FROM', '')
GROQ_KEY = os.environ.get('GROQ_KEY', '')
SHEET_ID = os.environ.get('SHEET_ID', '')
SHEET_CREDS = os.environ.get('SHEET_CREDS', '')

TICKERS_CONOCIDOS = {
    'sp500': 'SXR8.DE', 's&p500': 'SXR8.DE', 's&p 500': 'SXR8.DE',
    'world': 'EUNL.DE', 'msci world': 'EUNL.DE',
    'emerging': 'IS3N.DE', 'em': 'IS3N.DE', 'emergentes': 'IS3N.DE',
    'bitcoin': 'BTC-USD', 'btc': 'BTC-USD',
    'ethereum': 'ETH-USD', 'eth': 'ETH-USD',
    'aave': 'AAVE-USD', 'uniswap': 'UNI1-USD', 'uni': 'UNI1-USD',
    'yearn': 'YFI-USD', 'yfi': 'YFI-USD',
    'arbitrum': 'ARB11841-USD', 'arb': 'ARB11841-USD',
    'ypf': 'YPF', 'galicia': 'GGAL.BA', 'ggal': 'GGAL.BA',
    'mercadolibre': 'MELI.BA', 'meli': 'MELI.BA',
}

MANUAL_TICKERS = {
    'pension': 'MANUAL_4', 'pensión': 'MANUAL_4',
    'nordnet': 'MANUAL_1', 'revolut': 'MANUAL_2',
    'nordea': 'MANUAL_3',
}

NOMBRES = {
    'SXR8.DE': 'iShares Core S&P 500 UCITS ETF',
    'EUNL.DE': 'iShares Core MSCI World UCITS ETF',
    'IS3N.DE': 'iShares Core MSCI EM IMI UCITS ETF',
    'SPIUSGKL.CO': 'Sparinvest INDEX USA Growth KL',
    'SPVIGAKL.CO': 'Sparinvest INDEX Glob Akt KL',
    'SPIEMIKL.CO': 'Sparinvest INDEX Emerging Mkts KL',
    'SPVIBGKL.CO': 'Sparindex INDEX Baereditygtige Global KL',
    'SPIEUVKL.CO': 'Sparinvest INDEX Europa Value KL',
    'BTC-USD': 'Bitcoin', 'ETH-USD': 'Ethereum', 'AAVE-USD': 'Aave',
    'UNI1-USD': 'Uniswap', 'YFI-USD': 'yearn.finance',
    'ARB11841-USD': 'Arbitrum', 'YPF': 'YPF S.A.',
    'GGAL.BA': 'Grupo Financiero Galicia', 'MELI.BA': 'MercadoLibre',
    'MANUAL_1': 'Nordnet One Forsigtig', 'MANUAL_2': 'Revolut Flexible Funds',
    'MANUAL_3': 'Nordea Savings account', 'MANUAL_4': 'Pension',
}

# Guess type and currency from ticker
def guess_type(ticker):
    t = ticker.upper()
    if t in ['BTC-USD','ETH-USD','AAVE-USD','UNI1-USD','YFI-USD','ARB11841-USD']: return 'Crypto'
    if t.endswith('.BA'): return 'Stock'
    if t in ['YPF']: return 'Stock'
    if t.startswith('MANUAL_'): return 'Manual'
    return 'ETF'

def guess_currency(ticker):
    t = ticker.upper()
    if t.endswith('-USD') or t in ['YPF']: return 'USD'
    if t.endswith('.BA'): return 'ARS'
    if t.startswith('MANUAL_'): return 'DKK'
    return 'DKK'

def guess_account(ticker):
    t = ticker.upper()
    if t in ['BTC-USD','ETH-USD','AAVE-USD','UNI1-USD','YFI-USD','ARB11841-USD']: return 'Crypto wallet'
    if t.endswith('.BA') or t == 'YPF': return 'Arg based'
    if t.startswith('MANUAL_'): return 'Manual'
    return 'Nordnet regular'


def get_spreadsheet():
    creds_dict = json.loads(SHEET_CREDS)
    creds = Credentials.from_service_account_info(creds_dict, scopes=[
        'https://spreadsheets.google.com/feeds',
        'https://www.googleapis.com/auth/drive'
    ])
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID)


def get_sheet(name):
    return get_spreadsheet().worksheet(name)


def ticker_exists_in_portfolio(ticker):
    ws = get_sheet('Portfolio')
    col_a = ws.col_values(1)
    return ticker.upper() in [v.upper() for v in col_a]


def add_ticker_to_portfolio(ticker, name):
    ws = get_sheet('Portfolio')
    col_a = ws.col_values(1)

    # Find TOTAL row
    total_row = None
    for i, val in enumerate(col_a):
        if val == 'TOTAL':
            total_row = i + 1
            break
    if not total_row:
        return False

    typ = guess_type(ticker)
    cur = guess_currency(ticker)
    acc = guess_account(ticker)
    r = total_row  # insert before TOTAL

    ws.insert_row([''] * 14, r)

    # Build formulas for the new row
    sym_display = '—' if ticker.startswith('MANUAL_') else ticker
    is_manual = ticker.startswith('MANUAL_')

    if cur == 'DKK':
        pdkk_f = '=F' + str(r)
    elif cur == 'EUR':
        pdkk_f = '=F' + str(r) + '*FX!$B$2'
    elif cur == 'USD':
        pdkk_f = '=F' + str(r) + '*FX!$B$3'
    elif cur == 'ARS':
        pdkk_f = '=F' + str(r) + '*FX!$B$4'
    else:
        pdkk_f = '=F' + str(r)

    n = len(col_a)  # approx last data row
    qty_f = '=IFERROR(SUMIF(Transacciones!$B:$B,"' + ticker + '",Transacciones!$D:$D),"")' if not is_manual else ''
    cost_f = '=IFERROR(SUMIF(Transacciones!$B:$B,"' + ticker + '",Transacciones!$G:$G),"")' if not is_manual else ''

    ws.update('A' + str(r), [[
        sym_display, name, acc, typ,
        qty_f,
        '',         # price — filled by script
        cur,
        pdkk_f,
        '=IFERROR(H' + str(r) + '*E' + str(r) + ',"")',
        cost_f,
        '=IFERROR(I' + str(r) + '-J' + str(r) + ',"")',
        '=IFERROR((I' + str(r) + '-J' + str(r) + ')/J' + str(r) + ',"")',
        '=IFERROR(I' + str(r) + '/SUM($I$3:$I$100),"")',
        'Auto' if not is_manual else 'Manual',
    ]], value_input_option='USER_ENTERED')

    return True


def append_transaction(date, ticker, name, qty, price, currency, commission_dkk, notes):
    ws = get_sheet('Transacciones')
    fx = {'EUR': 7.46, 'USD': 6.89, 'DKK': 1.0, 'ARS': 0.0046}
    rate = fx.get(currency.upper(), 1.0)
    cost_dkk = round(qty * price * rate, 0)
    row = [date, ticker.upper(), name, qty, price, currency.upper(), cost_dkk, float(commission_dkk), notes]
    ws.append_row(row, value_input_option='USER_ENTERED')
    return cost_dkk


def parse_with_groq(text):
    url = 'https://api.groq.com/openai/v1/chat/completions'
    today = datetime.today().strftime('%Y-%m-%d')
    prompt = (
        'Sos un asistente financiero. Extrae los datos de esta transaccion.\n\n'
        'Devuelve SOLO un JSON valido (sin markdown, sin explicacion):\n'
        '{"ticker": "SXR8.DE", "qty": 10, "price": 44.50, "currency": "EUR", "date": "2026-05-14", "commission_dkk": 0, "notes": ""}\n\n'
        'Reglas:\n'
        '- ticker: simbolo exacto, nombre del activo, o pension/nordnet/revolut/nordea para manuales\n'
        '- qty: cantidad de unidades. Para pension/nordnet/revolut/nordea, qty es el valor total en DKK\n'
        '- price: precio por unidad. Para pension/nordnet/revolut/nordea, price es 1\n'
        '- currency: EUR, USD, DKK o ARS\n'
        '- date: fecha YYYY-MM-DD. Si no se menciona usar ' + today + '\n'
        '- commission_dkk: comision en DKK, 0 si no se menciona\n'
        '- notes: nota adicional o vacio\n\n'
        'Si faltan ticker/qty/price devuelve {"error": "faltan datos"}\n'
        'Si no es transaccion devuelve {"error": "no es una transaccion"}\n\n'
        'Mensaje:\n' + text
    )
    try:
        resp = requests.post(url,
            headers={'Authorization': 'Bearer ' + GROQ_KEY, 'Content-Type': 'application/json'},
            json={'model': 'llama-3.3-70b-versatile', 'messages': [{'role': 'user', 'content': prompt}], 'temperature': 0.1},
            timeout=30
        )
        data = resp.json()
        raw = data['choices'][0]['message']['content'].strip()
        raw = re.sub(r'```json|```', '', raw).strip()
        return json.loads(raw)
    except Exception as e:
        print('GROQ ERROR:', str(e), flush=True)
        return {'error': str(e)}


def send_whatsapp(to, message):
    client = Client(TWILIO_SID, TWILIO_TOKEN)
    client.messages.create(body=message, from_=TWILIO_FROM, to=to)


@app.route('/webhook', methods=['POST'])
def webhook():
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '').strip()

    if body.lower() in ['ayuda', 'help', 'hola', '?']:
        send_whatsapp(from_number,
            'Portfolio Bot\n\n'
            'Ejemplos:\n'
            'SXR8.DE 5 680 EUR\n'
            'compre 0.01 bitcoin a 95000 USD\n'
            'SXR8.DE 5 680 EUR fecha 2026-05-10 comision 45 DKK\n'
            'pension 52000 DKK\n'
            'nordea 25000 DKK\n'
            'NOVO-B.CO 10 850 DKK'
        )
        return 'OK', 200

    result = parse_with_groq(body)

    if 'error' in result:
        send_whatsapp(from_number,
            'No pude interpretar el mensaje.\n'
            'Manda "ayuda" para ver los formatos.'
        )
        return 'OK', 200

    ticker = result.get('ticker', '')
    qty = result.get('qty')
    price = result.get('price')
    currency = result.get('currency', 'DKK')
    date = result.get('date', datetime.today().strftime('%Y-%m-%d'))
    commission_dkk = result.get('commission_dkk', 0) or 0
    notes = result.get('notes', '') or ''

    # Resolver ticker
    ticker_lower = ticker.lower().strip()
    if ticker_lower in MANUAL_TICKERS:
        ticker = MANUAL_TICKERS[ticker_lower]
        currency = 'DKK'
        price = 1
    elif ticker_lower in TICKERS_CONOCIDOS:
        ticker = TICKERS_CONOCIDOS[ticker_lower]

    if not all([ticker, qty, price]):
        send_whatsapp(from_number, 'Faltan datos. Manda "ayuda" para ver los formatos.')
        return 'OK', 200

    name = NOMBRES.get(ticker.upper(), ticker.upper())

    # Agregar al Portfolio si es ticker nuevo
    new_in_portfolio = False
    if not ticker.startswith('MANUAL_') and not ticker_exists_in_portfolio(ticker):
        added = add_ticker_to_portfolio(ticker, name)
        new_in_portfolio = added

    try:
        cost_dkk = append_transaction(date, ticker, name, float(qty), float(price),
                                       currency, float(commission_dkk), notes)
        msg = (
            'Transaccion agregada\n'
            + name + ' (' + ticker + ')\n'
            + 'Fecha: ' + date + '\n'
            + 'Cantidad: ' + str(qty) + '\n'
            + 'Precio: ' + str(price) + ' ' + currency + '\n'
            + 'Costo: ' + str(int(cost_dkk)) + ' DKK'
        )
        if float(commission_dkk) > 0:
            msg += '\nComision: ' + str(int(float(commission_dkk))) + ' DKK'
        if new_in_portfolio:
            msg += '\nNuevo activo agregado al Portfolio'
        send_whatsapp(from_number, msg)
    except Exception as e:
        send_whatsapp(from_number, 'Error al guardar: ' + str(e))

    return 'OK', 200


@app.route('/', methods=['GET'])
def health():
    return 'OK', 200


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
