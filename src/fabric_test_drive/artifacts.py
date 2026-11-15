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
    F.col("d.OrderID").alias("order_id"),
    F.col("d.ProductID").alias("product_id"),
    F.col("o.CustomerID").alias("customer_id"),
    F.col("o.EmployeeID").alias("employee_id"),
    F.to_date("o.OrderDate").alias("order_date"),
    F.col("d.Quantity").alias("quantity"),
    F.col("d.UnitPrice").cast("decimal(18,4)").alias("unit_price"),
    F.col("d.Discount").alias("discount"),
    (F.col("d.UnitPrice") * F.col("d.Quantity") *
     (1 - F.col("d.Discount"))).cast("decimal(18,4)").alias("sales_amount"),
)
assert fact.filter("order_date IS NULL OR sales_amount IS NULL").count() == 0
fact.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("fact_sales")
assert spark.table("fact_sales").count() == counts["order_details"]
sales = spark.table("fact_sales").agg(F.sum("sales_amount")).first()[0]
assert sales is not None and sales > 0, "Sales query returned no positive revenue"
print(f"Verified fact_sales: {counts['order_details']} rows; sales amount: {sales}")
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
