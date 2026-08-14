from __future__ import annotations


BRAND_ALIASES = {
    "مرسدس بنز": "Mercedes-Benz",
    "مرسدس": "Mercedes-Benz",
    "بنز": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz",
    "mercedes-benz": "Mercedes-Benz",

    "بی ام و": "BMW",
    "بی‌ام‌و": "BMW",
    "ب ام و": "BMW",
    "bmw": "BMW",

    "پژو": "Peugeot",
    "peugeot": "Peugeot",

    "ایران خودرو": "Iran Khodro",
    "ایران‌خودرو": "Iran Khodro",

    "سایپا": "Saipa",
    "کیا": "Kia",
    "kia": "Kia",
    "هیوندای": "Hyundai",
    "hyundai": "Hyundai",
    "تویوتا": "Toyota",
    "toyota": "Toyota",
    "رنو": "Renault",
    "renault": "Renault",
    "نیسان": "Nissan",
    "مزدا": "Mazda",
    "فولکس واگن": "Volkswagen",
    "فولکس‌واگن": "Volkswagen",
}


MODEL_ALIASES = {
    "۲۰۶": "206",
    "206": "206",

    "۲۰۷": "207",
    "207": "207",

    "۴۰۵": "405",
    "405": "405",

    "پارس": "Pars",
    "پرشیا": "Pars",

    # Dena Plus is represented as model=Dena and trim=Plus
    # in the project catalog.
    "دنا پلاس": "Dena",
    "دناپلاس": "Dena",
    "دنا": "Dena",
    "dena": "Dena",

    # Soren variants are represented as model=Samand and
    # trim=Soren/Soren Plus in the project catalog.
    "سورن پلاس": "Samand",
    "سورن": "Samand",
    "سمند": "Samand",

    "تارا": "Tara",
    "رانا": "Runna",

    "پراید": "Pride",
    "شاهین": "Shahin",
    "کوئیک": "Quick",
    "کوییک": "Quick",
    "تیبا": "Tiba",
    "ساینا": "Saina",

    "ال ۹۰": "L90",
    "ال90": "L90",
    "ال نود": "L90",
    "تندر ۹۰": "L90",
    "تندر90": "L90",
    "l90": "L90",

    "ساندرو": "Sandero",
    "مگان": "Megane",
    "لوگان": "Logan",

    "سراتو": "Cerato",
    "cerato": "Cerato",
    "اپتیما": "Optima",
    "اسپورتیج": "Sportage",

    "النترا": "Elantra",
    "elantra": "Elantra",
    "سوناتا": "Sonata",
    "sonata": "Sonata",
    "توسان": "Tucson",
    "سانتافه": "Santa Fe",

    "کمری": "Camry",
    "camry": "Camry",
    "کرولا": "Corolla",
    "پرادو": "Prado",

    "slk": "SLK",
    "c-class": "C-Class",
    "c class": "C-Class",
}


# General trim aliases that are safe to match in the title
# after filtering them to the current brand/model catalog.
TRIM_ALIASES = {
    "تیپ شش": "تیپ 6",
    "تیپ ۶": "تیپ 6",
    "تیپ6": "تیپ 6",
    "تیپ 6": "تیپ 6",

    "تیپ پنج": "تیپ 5",
    "تیپ ۵": "تیپ 5",
    "تیپ5": "تیپ 5",
    "تیپ 5": "تیپ 5",

    "تیپ سه": "تیپ 3",
    "تیپ ۳": "تیپ 3",
    "تیپ3": "تیپ 3",
    "تیپ 3": "تیپ 3",

    "تیپ دو": "تیپ 2",
    "تیپ ۲": "تیپ 2",
    "تیپ2": "تیپ 2",
    "تیپ 2": "تیپ 2",

    "v8": "صندوقدار V8",
    "وی 8": "صندوقدار V8",
    "v9": "صندوقدار V9",
    "وی 9": "صندوقدار V9",

    "دنده ای": "دنده‌ای",
    "دنده‌ای": "دنده‌ای",
    "دستی": "دنده‌ای",
    "اتومات": "اتوماتیک",
    "اتوماتیک": "اتوماتیک",

    "mc": "MC",

    "glx": "GLX",
    "slx": "SLX",
    "elx": "ELX",
    "lx": "LX",

    "پلاس توربو اتوماتیک": "پلاس توربو اتوماتیک",
    "پلاس توربو اتومات": "پلاس توربو اتوماتیک",
    "توربو اتوماتیک": "پلاس توربو اتوماتیک",
    "توربو اتومات": "پلاس توربو اتوماتیک",
    "پلاس توربو": "پلاس توربو",
    "معمولی": "معمولی",
    "پلاس": "پلاس",

    "سورن پلاس": "سورن پلاس",
    "سورن": "سورن",
    "ef7": "EF7",

    "v1": "V1",
    "v2": "V2",
    "v3": "V3",
    "v4": "V4",

    "e1": "E1",
    "e2": "E2",

    "c180": "C180",
    "c200": "C200",
    "c230": "C230",
    "c250": "C250",
    "c300": "C300",
}


# These trims are too short, numeric, or semantically broad
# to be matched anywhere in the title. They require an alias
# that also contains the model name.
CONTEXT_ONLY_TRIMS = {
    "سال",
    "111",
    "131",
    "132",
    "151",
    "G",
    "GL",
    "R",
    "S",
    "1600",
    "2000",
    "200",
    "250",
    "280",
    "350",
    "550",
}


CONTEXTUAL_TRIM_ALIASES: dict[
    tuple[str, str],
    dict[str, str],
] = {
    ("Peugeot", "Pars"): {
        "پارس سال": "سال",
        "پرشیا سال": "سال",
    },
    ("Saipa", "Pride"): {
        "پراید 111": "111",
        "پراید 131": "131",
        "پراید 132": "132",
        "پراید 151": "151",
    },
    ("Saipa", "Shahin"): {
        "شاهین gl": "GL",
        "شاهین g": "G",
    },
    ("Saipa", "Quick"): {
        "کوییک r": "R",
        "کوئیک r": "R",
        "کوییک s": "S",
        "کوئیک s": "S",
    },
    ("Kia", "Cerato"): {
        "سراتو 1600": "1600",
        "cerato 1600": "1600",
        "سراتو 2000": "2000",
        "cerato 2000": "2000",
    },
    ("Mercedes-Benz", "SLK"): {
        "slk 200": "200",
        "slk 250": "250",
        "slk 280": "280",
        "slk 350": "350",
        "slk 550": "550",
    },
}
