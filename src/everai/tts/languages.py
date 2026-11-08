"""Language names and supported ElevenLabs models used by the voice picker."""
# Model coverage checked against https://elevenlabs.io/docs/overview/models on 2026-10-22.
LANGUAGES = dict(
    af='Afrikaans', am='Amharic', ar='Ả Rập', as_='Assam', ast='Asturian', az='Azerbaijan',
    be='Belarus', bg='Bulgaria', bn='Bengal', bs='Bosnia', ca='Catalan', ceb='Cebuano',
    cs='Séc', cy='Wales', da='Đan Mạch', de='Đức', el='Hy Lạp', en='Anh', es='Tây Ban Nha',
    et='Estonia', eu='Basque', fa='Ba Tư', fi='Phần Lan', fil='Filipino', fr='Pháp',
    ga='Ireland', gl='Galicia', gu='Gujarati', ha='Hausa', he='Do Thái', hi='Hindi',
    hr='Croatia', hu='Hungary', hy='Armenia', id='Indonesia', is_='Iceland', it='Ý',
    ja='Nhật', jv='Java', ka='Gruzia', kk='Kazakh', km='Khmer', kn='Kannada', ko='Hàn',
    ky='Kyrgyz', lb='Luxembourg', ln='Lingala', lo='Lào', lt='Litva', lv='Latvia',
    mi='Māori', mk='Macedonia', ml='Malayalam', mn='Mông Cổ', mr='Marathi', ms='Mã Lai',
    mt='Malta', my='Miến Điện', nb='Na Uy (Bokmål)', ne='Nepal', nl='Hà Lan', no='Na Uy',
    ny='Chichewa', oc='Occitan', or_='Odia', pa='Punjab', pl='Ba Lan', ps='Pashto',
    pt='Bồ Đào Nha', ro='Romania', ru='Nga', sd='Sindhi', si='Sinhala', sk='Slovak',
    sl='Slovenia', so='Somali', sq='Albania', sr='Serbia', su='Sunda', sv='Thụy Điển',
    sw='Swahili', ta='Tamil', te='Telugu', tg='Tajik', th='Thái', tr='Thổ Nhĩ Kỳ',
    uk='Ukraine', ur='Urdu', uz='Uzbek', vi='Việt', yo='Yoruba', yue='Quảng Đông', zh='Trung', zu='Zulu')
for code in ('as', 'is', 'or'):
    LANGUAGES[code] = LANGUAGES.pop(code + '_')

MULTILINGUAL = 'en ja zh de hi fr ko pt it es id nl tr fil pl sv bg ro ar cs el fi hr ms sk da ta uk ru'.split()
V3 = 'af ar hy as ast az be bn bs bg my yue ca ceb ny hr cs da nl en et fil fi fr gl ka de el gu ha he hi hu is id ga it ja jv kn kk ky ko lv ln lt lb mk ms ml mt zh mi mr mn ne no oc or ps fa pl pt pa ro ru sr sd sk sl so es sw sv tg ta te th tr uk ur uz vi cy yo'.split()
ELEVEN_MODELS = {
    'eleven_v3': V3,
    'eleven_v4': [code for code in V3 if code != 'ny'],
    'eleven_flash_v2_5': MULTILINGUAL + ['hu', 'no', 'vi'],
    'eleven_multilingual_v2': MULTILINGUAL,
}


def language_label(code):
    return f"{LANGUAGES.get(code, code)} ({code})"


def language_code(label):
    return label.rsplit('(', 1)[-1].rstrip(')') if label != 'Tất cả' else None
