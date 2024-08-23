import requests
import json
import csv
import datetime

# Define variables to match your environment
api_url = "your_api_url"  # Change this to your server
api_key = "your_api_key"  # Change this to your own API key


def generate_name_id_mapping(entity_name):
    url = api_url + f"/objects/{entity_name}"
    response = requests.get(url, headers={"accept": "application/json", "GROCY-API-KEY": api_key})
    data = response.json()
    mapping = {}
    for item in data:
        mapping[item["name"]] = item["id"]
    return mapping


# Example mappings (replace with actual logic)
location_name_to_id_map = generate_name_id_mapping("locations")
product_group_name_to_id_map = generate_name_id_mapping("product_groups")
quantity_unit_name_to_id_map = generate_name_id_mapping("quantity_units")


def post_data(entity_name, data):
    url = api_url + f"/objects/{entity_name}"
    headers = {"accept": "application/json", "Content-Type": "application/json", "GROCY-API-KEY": api_key}
    response = requests.post(url, headers=headers, data=json.dumps(data))
    return response


def put_data(entity_name, data):
    url = api_url + f"/objects/{entity_name}/{data['id']}"
    headers = {"accept": "application/json", "Content-Type": "application/json", "GROCY-API-KEY": api_key}
    response = requests.put(url, headers=headers, data=json.dumps(data))
    return response


def get_conversion_id(product_id, from_id, to_id):
    # generate the url based on the product_id, from_id and to_id
    url = api_url + f"/objects/quantity_unit_conversions?query%5B%5D=product_id%3D{product_id}&query%5B%5D=from_qu_id%3D{from_id}&query%5B%5D=to_qu_id%3D{to_id}"
    headers = {"accept": "application/json", "GROCY-API-KEY": api_key}
    response = requests.get(url, headers=headers)
    data = response.json()
    if len(data) > 0:
        return data[0]["id"]
    else:
        raise Exception("Conversion not found")


def get_product_id(product_name):
    url = api_url + f"/objects/products?query%5B%5D=name%3D{product_name}"
    headers = {"accept": "application/json", "GROCY-API-KEY": api_key}
    response = requests.get(url, headers=headers)
    data = response.json()
    if len(data) > 0:
        return data[0]["id"]
    else:
        return None


def get_max_id(entity_name):
    url = api_url + f"/objects/{entity_name}"
    headers = {"accept": "application/json", "GROCY-API-KEY": api_key}
    response = requests.get(url, headers=headers)
    data = response.json()
    return max([item["id"] for item in data])


def import_from_csv(input_file):
    # process csv file row by row
    with open(input_file, "r") as file:
        reader = csv.reader(file)
        lines = list(reader)[1:]
        for row in lines:
            name, location_name, quantity_unit_name, product_group_name, min_stock_amount, qu_purchase_name, qu_factor_purchase_to_stock = row
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            product_id = get_product_id(name)
            product_data = {
                "name": name,
                "row_created_timestamp": timestamp,
                "location_id": location_name_to_id_map[location_name],
                "product_group_id": product_group_name_to_id_map[product_group_name],
                "qu_id_purchase": quantity_unit_name_to_id_map[qu_purchase_name],
                "qu_id_stock": quantity_unit_name_to_id_map[quantity_unit_name],
                "min_stock_amount": int(min_stock_amount) if len(min_stock_amount) > 0 else 0,
            }
            if product_id is None:
                product_id = get_max_id("products") + 1
                product_data["id"] = product_id
                response = post_data("products", product_data)
                if response.status_code != 200:
                    print(f"Error creating product {name}: {response.status_code}, {response.text}")
                    print(product_data)
                    print("-----------------------------------")
                    raise Exception("Error creating product")

            from_id = product_data["qu_id_purchase"]
            to_id = product_data["qu_id_stock"]
            if from_id != to_id:
                conversion_id = get_conversion_id(product_id, from_id, to_id)

                conversion_data = {
                    "id": conversion_id,
                    "from_qu_id": from_id,
                    "to_qu_id": to_id,
                    "factor": float(qu_factor_purchase_to_stock),
                    "product_id": product_id,
                    "row_created_timestamp": timestamp,
                }
                response = put_data("quantity_unit_conversions", conversion_data)
                if response.status_code != 204:
                    print(response.status_code, response.text)
            print(f"Product {name} processed successfully. Product ID: {product_id}")


def get_invalid_due_date_products():
    food_groups = [1, 2, 3, 4, 5, 7, 8, 9, 10, 14, 15, 16, 18, 19]
    url = api_url + f"/objects/products"
    response = requests.get(url, headers={"accept": "application/json", "GROCY-API-KEY": api_key})
    data = response.json()
    data = [d for d in data if d["product_group_id"] in food_groups]
    desired_keys = ["id", "name", "default_best_before_days"]
    return [{k: item[k] for k in desired_keys} for item in data if item["default_best_before_days"] is None or item["default_best_before_days"] == 0]

def update_due_dates_from_csv(csv_file):
    with open(csv_file, "r",encoding="utf-8") as file:
        reader = csv.reader(file)
        lines = list(reader)[1:]
        for row in lines:
            pid,name,default_best_before_days,default_best_before_days_after_freezing,default_best_before_days_after_open = row
            pid = int(pid)
            product_id = get_product_id(name)
            if pid != product_id:
                print(f"Product ID mismatch for {name}. Expected: {product_id}, Found: {pid}")
                continue
            data = {
                "id": int(product_id),
                "default_best_before_days": int(default_best_before_days),
            }
            if default_best_before_days_after_freezing:
                data["default_best_before_days_after_freezing"] = int(default_best_before_days_after_freezing)
            if default_best_before_days_after_open:
                data["default_best_before_days_after_open"] = int(default_best_before_days_after_open)
            print(f"Updating product {name} with data {data}")
            response = put_data("products", data)
            if response.status_code != 204:
                print(response.status_code, response.text)
            else:
                print(f"Product {name} updated successfully")
  

if __name__ == "__main__":
    import_from_csv(input_file = "import_grocy.csv")
    # update_due_dates_from_csv("due_dates_grocy.csv")
