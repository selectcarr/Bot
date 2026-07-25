from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleDefinition:
    brand: str
    model: str
    trims: tuple[str, ...] = ()


VEHICLE_CATALOG = (
    VehicleDefinition(
        brand="Peugeot",
        model="206",
        trims=(
            "تیپ 2",
            "تیپ 3",
            "تیپ 5",
            "تیپ 6",
            "صندوقدار V8",
            "صندوقدار V9",
        ),
    ),
    VehicleDefinition(
        brand="Peugeot",
        model="207",
        trims=(
            "دنده‌ای",
            "اتوماتیک",
            "MC",
        ),
    ),
    VehicleDefinition(
        brand="Peugeot",
        model="405",
        trims=(
            "GLX",
            "SLX",
        ),
    ),
    VehicleDefinition(
        brand="Peugeot",
        model="Pars",
        trims=(
            "سال",
            "LX",
            "ELX",
        ),
    ),
    VehicleDefinition(
        brand="Iran Khodro",
        model="Dena",
        trims=(
            "معمولی",
            "پلاس",
            "پلاس توربو",
            "پلاس توربو اتوماتیک",
        ),
    ),
    VehicleDefinition(
        brand="Iran Khodro",
        model="Samand",
        trims=(
            "LX",
            "EF7",
            "سورن",
            "سورن پلاس",
        ),
    ),
    VehicleDefinition(
        brand="Iran Khodro",
        model="Tara",
        trims=(
            "V1",
            "V2",
            "V3",
            "V4",
        ),
    ),
    VehicleDefinition(
        brand="Saipa",
        model="Pride",
        trims=(
            "111",
            "131",
            "132",
            "151",
        ),
    ),
    VehicleDefinition(
        brand="Saipa",
        model="Shahin",
        trims=(
            "G",
            "GL",
            "اتوماتیک",
        ),
    ),
    VehicleDefinition(
        brand="Saipa",
        model="Quick",
        trims=(
            "دنده‌ای",
            "اتوماتیک",
            "R",
            "S",
        ),
    ),
    VehicleDefinition(
        brand="Renault",
        model="L90",
        trims=(
            "E1",
            "E2",
            "پلاس",
            "اتوماتیک",
        ),
    ),
    VehicleDefinition(
        brand="Kia",
        model="Cerato",
        trims=(
            "1600",
            "2000",
        ),
    ),
    VehicleDefinition(
        brand="Hyundai",
        model="Elantra",
    ),
    VehicleDefinition(
        brand="Hyundai",
        model="Sonata",
    ),
    VehicleDefinition(
        brand="Toyota",
        model="Camry",
    ),
    VehicleDefinition(
        brand="Mercedes-Benz",
        model="SLK",
        trims=(
            "200",
            "250",
            "280",
            "350",
            "550",
        ),
    ),
    VehicleDefinition(
        brand="Mercedes-Benz",
        model="C-Class",
        trims=(
            "C180",
            "C200",
            "C230",
            "C250",
            "C300",
        ),
    ),
)


def get_models_for_brand(brand: str) -> tuple[str, ...]:
    return tuple(
        vehicle.model
        for vehicle in VEHICLE_CATALOG
        if vehicle.brand == brand
    )


def get_trims(
    brand: str,
    model: str,
) -> tuple[str, ...]:
    for vehicle in VEHICLE_CATALOG:
        if (
            vehicle.brand == brand
            and vehicle.model == model
        ):
            return vehicle.trims

    return ()


def is_valid_vehicle(
    brand: str,
    model: str,
    trim: str = "",
) -> bool:
    for vehicle in VEHICLE_CATALOG:
        if (
            vehicle.brand != brand
            or vehicle.model != model
        ):
            continue

        if not trim:
            return True

        if not vehicle.trims:
            return True

        return trim in vehicle.trims

    return False
