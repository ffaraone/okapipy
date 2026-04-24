# okapipy

## Overview

**okapipy** is a **Python client generator** that produces strongly-typed API clients from an OpenAPI specification.

The generated client follows a **fluent interface design pattern** and is based on a **hierarchical resource model** composed of:

* Namespaces
* Collections
* Resources
* Actions

This approach enables intuitive, discoverable, and scalable API interactions.

---

## Design Principles

### 1. Hierarchical API Model

The generator assumes that APIs follow a structured hierarchy:

```
namespace → collection → resource → subcollection → action
```

Example:

```
/admin/organizations/{orgId}/accounts/{accountId}/users/{userId}/activate
```

This hierarchy is directly reflected in the generated client.

---

### 2. Fluent Interface

The generated client allows chaining operations to naturally navigate the API:

```python
org = client.admin.organizations["org-123"].retrieve()
org = await client.admin.organizations["org-123"].aretrieve()
```

This mirrors the API structure and improves readability and developer experience.

---

### 3. Strong Typing

* Full IDE autocomplete support
* Embedded API documentation (docstring are generated from the api documentation)
* Type-safe interactions
* The client should support for requests and responses both dictionaries or pydantic 2 models generated from openapi spec.

---

### 4. Static Code Generation

* No runtime reflection or dynamic client building
* Fully generated Python code
* Optimized for performance and developer tooling


---

## Features

* OpenAPI 3.x support
* Fluent API navigation
* Sync and async client support
* Typed models (Pydantic v2 compatible)
* Customizable pagination support
* Support for:

  * Path parameters
  * Query parameters
  * Request bodies
  * Actions (non-CRUD operations)
* Arbitrary nesting depth

---

## Example Usage

### Count objects in a collection

```python
org_count = client.admin.organizations.all().count()
org_count = await client.admin.organizations.all().acount()
org_count = client.admin.organizations.query(query).count()
org_count = await client.admin.organizations.query(query).acount()
```
### Iterate objects in a collection (automatic pagination management)

```python
for org in client.admin.organizations.all():
    print(org)

for org in client.admin.organizations.query(query):
    print(org)

async for org in client.admin.organizations.all():
    print(org)

async for org in client.admin.organizations.query(query):
    print(org)

```

### Nested Resources

```python
client.admin.organizations[org_id].accounts[account_id].get()
await client.admin.organizations[org_id].accounts[account_id].aget()
```

### Actions

```python
client.admin.users[user_id].activate.post()

await client.admin.users[user_id].activate.apost()
```

