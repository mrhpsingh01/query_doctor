CREATE TABLE orders (id bigint PRIMARY KEY, customer_id integer NOT NULL, created_at date NOT NULL);
INSERT INTO orders SELECT n, n % 5000, DATE '2024-01-01' + (n % 365) FROM generate_series(1, 250000) AS n;
CREATE INDEX orders_customer_created ON orders(customer_id, created_at);
