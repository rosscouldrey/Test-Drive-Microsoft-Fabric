import base64
import json

SOURCE = "https://services.odata.org/V4/Northwind/Northwind.svc/"
ENTITIES = {
    "Customers": "customers",
    "Orders": "orders",
    "Order_Details": "order_details",
    "Products": "products",
    "Categories": "categories",
    "Employees": "employees",
    "Suppliers": "suppliers",
    "Shippers": "shippers",
}
PROJECTIONS = {
    "Categories": ("CategoryID", "CategoryName", "Description"),
    "Employees": (
        "EmployeeID", "LastName", "FirstName", "Title", "TitleOfCourtesy",
        "BirthDate", "HireDate", "Address", "City", "Region", "PostalCode",
        "Country", "HomePhone", "Extension", "Notes", "ReportsTo", "PhotoPath",
    ),
}


def definition(path, content):
    return {"parts": [{
        "path": path,
        "payload": base64.b64encode(json.dumps(content).encode()).decode(),
        "payloadType": "InlineBase64",
    }]}


def pipeline(workspace, lakehouse, connection):
    activities = []
    for entity, table in ENTITIES.items():
        activities.append({
            "name": f"Copy_{table}",
            "type": "Copy",
            "dependsOn": (
                [{"activity": activities[-1]["name"], "dependencyConditions": ["Succeeded"]}]
                if activities else []
            ),
            "policy": {"timeout": "0.00:15:00", "retry": 2, "retryIntervalInSeconds": 30},
            "typeProperties": {
                "source": {
                    "type": "ODataSource",
                    "httpRequestTimeout": "00:05:00",
                    **({"query": "$select=" + ",".join(PROJECTIONS[entity])}
                       if entity in PROJECTIONS else {}),
                    "datasetSettings": {
                        "annotations": [], "type": "ODataResource", "schema": [],
                        "typeProperties": {"path": entity},
                        "externalReferences": {"connection": connection},
                    },
                },
                "sink": {
                    "type": "LakehouseTableSink",
                    "tableActionOption": "Overwrite",
                    "partitionOption": "None",
                    "datasetSettings": {
                        "annotations": [], "type": "LakehouseTable", "schema": [],
                        "typeProperties": {"table": table},
                        "linkedService": {
                            "name": "NorthwindLakehouse",
                            "properties": {
                                "annotations": [], "type": "Lakehouse",
                                "typeProperties": {
                                    "workspaceId": workspace, "artifactId": lakehouse,
                                    "rootFolder": "Tables",
                                },
                            },
                        },
                    },
                },
                "enableStaging": False,
            },
        })
    return definition("pipeline-content.json", {"properties": {"activities": activities}})


def notebook(workspace, lakehouse):
    code = '''from pyspark.sql import functions as F

tables = ("customers", "orders", "order_details", "products", "categories",
          "employees", "suppliers", "shippers")
counts = {name: spark.table(name).count() for name in tables}
assert all(count > 0 for count in counts.values()), f"Empty source tables: {counts}"
print(f"Verified source row counts: {counts}")

orders = spark.table("orders").alias("o")
details = spark.table("order_details").alias("d")
joined = details.join(orders, F.col("d.OrderID") == F.col("o.OrderID"))
assert joined.count() == counts["order_details"], "Unmatched or duplicate order keys"
fact = joined.select(
    F.col("d.OrderID").cast("bigint").alias("order_id"),
    F.col("d.ProductID").cast("bigint").alias("product_id"),
    F.col("o.CustomerID").alias("customer_id"),
    F.col("o.EmployeeID").cast("bigint").alias("employee_id"),
    F.to_date("o.OrderDate").alias("order_date"),
    F.col("d.Quantity").cast("bigint").alias("quantity"),
    F.col("d.UnitPrice").cast("decimal(18,4)").alias("unit_price"),
    F.col("d.Discount").cast("double").alias("discount"),
    (F.col("d.UnitPrice") * F.col("d.Quantity") *
     (1 - F.col("d.Discount"))).cast("decimal(18,4)").alias("sales_amount"),
)
assert fact.filter("order_date IS NULL OR sales_amount IS NULL").count() == 0

def save_gold(frame, name, *keys):
    assert frame.count() > 0, f"Empty gold table: {name}"
    assert frame.filter(" OR ".join(f"{key} IS NULL" for key in keys)).count() == 0, f"Null keys: {name}"
    assert frame.count() == frame.select(*keys).distinct().count(), f"Duplicate keys: {name}"
    frame.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(name)
    assert spark.table(name).count() == frame.count(), f"Persisted row count differs: {name}"

save_gold(spark.table("customers").select(
    F.col("CustomerID").alias("customer_id"),
    F.col("CompanyName").alias("company_name"),
    F.col("City").alias("city"), F.col("Country").alias("country"),
), "dim_customer", "customer_id")
categories = spark.table("categories").select("CategoryID", "CategoryName")
suppliers = spark.table("suppliers").select("SupplierID", F.col("CompanyName").alias("SupplierName"))
products = spark.table("products").join(categories, "CategoryID", "left").join(suppliers, "SupplierID", "left")
assert products.count() == counts["products"], "Duplicate product/category/supplier keys"
save_gold(products.select(
    F.col("ProductID").cast("bigint").alias("product_id"),
    F.col("ProductName").alias("product_name"),
    F.col("CategoryName").alias("category_name"),
    F.col("SupplierName").alias("supplier_name"),
), "dim_product", "product_id")
save_gold(spark.table("employees").select(
    F.col("EmployeeID").cast("bigint").alias("employee_id"),
    F.concat_ws(" ", "FirstName", "LastName").alias("employee_name"),
    F.col("Title").alias("title"), F.col("Country").alias("country"),
), "dim_employee", "employee_id")
bounds = fact.agg(F.min("order_date").alias("first_date"), F.max("order_date").alias("last_date")).first()
dates = spark.sql(
    f"SELECT explode(sequence(DATE'{bounds.first_date}', DATE'{bounds.last_date}')) AS date"
)
save_gold(dates.select(
    "date", F.year("date").cast("bigint").alias("year"),
    F.concat(F.lit("Q"), F.quarter("date")).alias("quarter"),
    F.month("date").cast("bigint").alias("month_number"),
    F.date_format("date", "MMM").alias("month"),
    F.date_format("date", "yyyy-MM").alias("year_month"),
), "dim_date", "date")
save_gold(fact, "fact_sales", "order_id", "product_id")
for dimension, key, fact_key in (
    ("dim_customer", "customer_id", "customer_id"),
    ("dim_product", "product_id", "product_id"),
    ("dim_employee", "employee_id", "employee_id"),
    ("dim_date", "date", "order_date"),
):
    keys = spark.table(dimension).select(F.col(key).alias(fact_key))
    assert spark.table("fact_sales").join(keys, fact_key, "left_anti").count() == 0, f"Unmatched {dimension} keys"
assert spark.table("fact_sales").count() == counts["order_details"]
sales = spark.table("fact_sales").agg(F.sum("sales_amount")).first()[0]
assert sales is not None and sales > 0, "Sales query returned no positive revenue"
print(f"Verified gold layer: {counts['order_details']} sales rows; sales amount: {sales}")
'''
    content = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"name": "synapse_pyspark", "display_name": "Synapse PySpark"},
            "language_info": {"name": "python"},
            "dependencies": {"lakehouse": {
                "default_lakehouse": lakehouse,
                "default_lakehouse_name": "Northwind",
                "default_lakehouse_workspace_id": workspace,
                "known_lakehouses": [{"id": lakehouse}],
            }},
        },
        "cells": [{
            "cell_type": "code", "execution_count": None,
            "metadata": {}, "outputs": [], "source": code.splitlines(keepends=True),
        }],
    }
    result = definition("notebook-content.ipynb", content)
    result["format"] = "ipynb"
    return result
