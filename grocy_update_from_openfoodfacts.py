import sys
import re
import base64
import logging
import datetime

from urllib import parse
import json
import requests

logger = logging.getLogger(__name__)

GROCY_API_URL = "your_api_url"
GROCY_API_KEY = "your_api_key"

# These units have to be present in your grocy instance
GRAM_NAME = "Gram"
KILOGRAM_NAME = "Kilogram"
MILLILITER_NAME = "Mililitre"
LITER_NAME = "Litre"

# File where all the barcodes that have been already processed will be listed
PROCESSED_BARCODES_FILEPATH = "./processed_barcodes.txt"

# If there is no conversion from the stock unit to grams or milliliters, ask the user before adding it automatically
ASK_BEFORE_ADDING_CONVERSION = True


def request_grocy(call_url, data=None, method="put"):
    url = parse.urljoin(GROCY_API_URL, call_url)
    headers = {"accept": "application/json", "GROCY-API-KEY": GROCY_API_KEY}
    if isinstance(data, dict):
        data = json.dumps(data)
        headers["Content-Type"] = "application/json"
    response = requests.request(method, url, headers=headers, data=data)
    if response.status_code >= 400:
        raise ValueError(f"Error using Grocy API: {response.status_code} - {response.text}")
    if method == "get":
        return response.json()


def get_grocy(call_url):
    return request_grocy(call_url, None, "get")


def delete_grocy(call_url):
    return request_grocy(call_url, None, "delete")


def post_grocy(call_url, data):
    return request_grocy(call_url, data, "post")


def put_grocy(call_url, data):
    return request_grocy(call_url, data, "put")


unit_name_to_id = {item["name"]: item["id"] for item in get_grocy("objects/quantity_units")}
unit_id_to_name = {v: k for k, v in unit_name_to_id.items()}


def get_open_data(barcode):
    logger.info(f"Querying OpenFoodFacts for barcode {barcode} ...")
    response = requests.get(f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json")
    if response.status_code >= 400:
        raise ValueError(f"Error getting data from OpenFoodFacts: {response.status_code} - {response.content}")
    data = response.json()
    if data["status"] == 0:
        response = requests.get(f"https://world.openbeautyfacts.org/api/v0/product/{barcode}.json")
        if response.status_code >= 400:
            raise ValueError(f"Error getting data from OpenBeautyFacts: {response.status_code} - {response.content}")
        data = response.json()
        if data["status"] == 0:
            raise ValueError(f"Error getting data for barcode {barcode} from OpenFoodFacts and OpenBeautyFacts")
    return data


def get_stock_conversion(product_details):
    product_id = product_details["product"]["id"]
    stock_unit = product_details["product"]["qu_id_stock"]
    grams_id = unit_name_to_id[GRAM_NAME]
    milliliters_id = unit_name_to_id[MILLILITER_NAME]
    conversion = get_conversion(product_id, stock_unit, grams_id)
    if conversion is None:
        conversion = get_conversion(product_id, stock_unit, milliliters_id)
    return conversion


def get_conversion(product_id, from_id, to_id):
    query_params = {"query[]": [f"product_id={product_id}", f"from_qu_id={from_id}", f"to_qu_id={to_id}"]}
    query = parse.urlencode(query_params, doseq=True)
    conversions = get_grocy(f"objects/quantity_unit_conversions_resolved?{query}")
    if len(conversions) == 0:
        return None
    return conversions[0]


def extract_amount_and_unit(open_data):
    # usually the amount is in open_data.product.product_quantity
    # but sometimes it is in open_data.product.quantity, together with the units
    # the quantity field sometimes has a space between the number and the unit, sometimes not
    if "product" not in open_data:
        raise ValueError(f"No product data in OpenFoodFacts response")

    if "quantity" not in open_data["product"] or len(open_data["product"]["quantity"]) == 0:
        raise ValueError(f"No quantity data in OpenFoodFacts response")

    match = re.match(r"(\d+\.?\d*)\s*(\w+)", open_data["product"]["quantity"].lower())
    if match:
        amount, amount_unit = match.groups()
    else:
        raise ValueError(f"Could not get amount and unit from OpenFoodFacts data: {open_data['product']['quantity']}")
    if "product_quantity" in open_data["product"]:
        if open_data["product"]["product_quantity"] != amount:
            logger.warning(f"Amounts do not match: {open_data['product']['product_quantity']} vs {amount}")
            # amount = open_data["product"]["product_quantity"]
    if amount_unit == "g":
        unit_id = unit_name_to_id[GRAM_NAME]
    elif amount_unit == "kg":
        unit_id = unit_name_to_id[KILOGRAM_NAME]
    elif amount_unit == "ml":
        unit_id = unit_name_to_id[MILLILITER_NAME]
    elif amount_unit == "l":
        unit_id = unit_name_to_id[LITER_NAME]
    else:
        raise ValueError(f"Unknown amount unit {amount_unit}")
    return float(amount), unit_id


def add_conversion(product_details, open_data, ask=ASK_BEFORE_ADDING_CONVERSION):
    product_name = product_details["product"]["name"]
    product_amount, amount_unit = extract_amount_and_unit(open_data)
    stock_id = product_details["product"]["qu_id_stock"]
    stock_unit_name = unit_id_to_name[stock_id]
    amount_unit_name = unit_id_to_name[amount_unit]
    product_id = product_details["product"]["id"]
    if ask:
        print(f"Product '{product_name}' (id {product_id}) has no conversion from stock unit {stock_unit_name} to g or ml.")
        print(f"Barcode {open_data['code']} product amount: {product_amount} {amount_unit_name}")
        user_answer = input(f"Add conversion to {amount_unit_name}? [y/n]: ")
        if user_answer.lower() != "y":
            return
    _, to_id = extract_amount_and_unit(open_data)
    conversion_data = {
        "product_id": product_id,
        "from_qu_id": stock_id,
        "to_qu_id": to_id,
        "factor": product_amount,
        # "row_created_timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    logger.info(f"Adding conversion from {amount_unit} to {stock_unit_name} for product {product_id}")
    post_grocy("objects/quantity_unit_conversions", conversion_data)


def add_picture_to_product(product_id, file_url) -> None:
    image_name = f"{product_id}.jpg"
    image_name64 = base64.b64encode(image_name.encode()).decode()
    image_request = requests.get(file_url)
    if image_request.status_code >= 400:
        raise ValueError(f"Error getting image from {file_url}: code {image_request.status_code}")
    try:
        put_grocy(f"files/productpictures/{image_name64}", image_request.content)
    except Exception as e:
        delete_grocy(f"files/productpictures/{image_name64}")
        put_grocy(f"files/productpictures/{image_name64}", image_request.content)
    put_grocy(f"objects/products/{product_id}", {"picture_file_name": image_name})


def get_calories(open_data, product_details):
    if "nutriments" in open_data["product"]:
        nutriments = open_data["product"]["nutriments"]
        if "energy-kcal_100g" in nutriments:
            calories_per_100g_or_ml = nutriments["energy-kcal_100g"]
            stock_conversion = get_stock_conversion(product_details)
            if stock_conversion is None:
                add_conversion(product_details, open_data)
                stock_conversion = get_stock_conversion(product_details)
            factor_to_apply = stock_conversion["factor"] / 100
            calories_per_stock_unit = calories_per_100g_or_ml * factor_to_apply
            return calories_per_stock_unit
    return None


def update_product_calories(product_details, open_data):
    current_calories = product_details["product"]["calories"]
    if current_calories is not None and current_calories > 1:
        logger.debug(f"Calories already set for product {product_details['product']['name']}")
        return
    calories = get_calories(open_data, product_details)
    if calories is None:
        logger.warning(f"Could not get calories for product {product_details['product']['name']}")
        return
    product_data = {"calories": calories}
    product_id = product_details["product"]["id"]
    put_grocy(f"objects/products/{product_id}", product_data)
    logger.info(f"Updated calories for product {product_details['product']['name']}")


def update_product_barcode(product_details, open_data):
    barcode_data = None
    # get matching barcode in product_barcodes
    for barcode_data in product_details["product_barcodes"]:
        if barcode_data["barcode"] == open_data["code"]:
            break
    if barcode_data is None:
        raise ValueError(f"Barcode {open_data['code']} not found in product {product_details['product']['name']}")
    updated = False
    if barcode_data["note"] is None:
        updated = True
        barcode_data["note"] = open_data["product"]["product_name"]
    if barcode_data["amount"] is None:
        try:
            amount, unit_id = extract_amount_and_unit(open_data)
            barcode_data["amount"] = amount
            barcode_data["qu_id"] = unit_id
        except ValueError as e:
            logger.warning(f"Could not extract amount and unit from OpenFoodFacts data")
        updated = True
    if updated:
        put_grocy(f"objects/product_barcodes/{barcode_data['id']}", barcode_data)
        logger.info(f"Updated barcode {barcode_data['barcode']} for product {product_details['product']['name']}")
    else:
        logger.info(f"Data already present in barcode {barcode_data['barcode']}")


def update_product_image(product_details, open_data, image_key="image_front_small_url"):
    product_id = product_details["product"]["id"]
    image_url = open_data["product"][image_key]
    if image_url is None:
        logger.debug(f"No image found for product {product_details['product']['name']}")
        return
    add_picture_to_product(product_id, image_url)
    logger.info(f"Added image for product {product_details['product']['name']}")


def barcode_processed(barcode, persistency_file=PROCESSED_BARCODES_FILEPATH):
    try:
        with open(persistency_file, "r") as file:
            processed_barcodes = set([line.strip() for line in file])
    except FileNotFoundError:
        processed_barcodes = set()
    return barcode in processed_barcodes


def save_processed_barcode(barcode, persistency_file=PROCESSED_BARCODES_FILEPATH):
    with open(persistency_file, "a") as file:
        file.write(f"{barcode}\n")


# the product data will be updated only using the data from its first barcode
processed_product_ids = set()


def update_barcode_from_openfoodfacts(barcode):
    try:
        if barcode_processed(barcode):
            logger.debug(f"Barcode {barcode} already processed")
            return
        logger.info(f"Updating product using barcode {barcode} ...")
        product_details = get_grocy(f"stock/products/by-barcode/{barcode}")
        open_data = get_open_data(barcode)

        update_product_barcode(product_details, open_data)

        product_id = product_details["product"]["id"]
        if product_id in processed_product_ids:
            logger.debug(f"Product id {product_id} already processed")
            return

        update_product_calories(product_details, open_data)
        update_product_image(product_details, open_data)

        logger.info(f"Updated product {product_details['product']['name']} using barcode {barcode}")
        processed_product_ids.add(product_id)
    except Exception as e:
        # logger.exception(e)
        logger.error(f"Error updating product from barcode {barcode}: {e}")
    finally:
        save_processed_barcode(barcode)


def get_list_of_barcodes_from_grocy():
    return [barcode["barcode"] for barcode in get_grocy("objects/product_barcodes")]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    barcodes_list = get_list_of_barcodes_from_grocy()
    for barcode in barcodes_list:
        update_barcode_from_openfoodfacts(barcode)
