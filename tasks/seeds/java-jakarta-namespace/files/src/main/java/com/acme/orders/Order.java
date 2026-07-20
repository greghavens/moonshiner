package com.acme.orders;

import javax.persistence.Column;
import javax.persistence.Entity;
import javax.persistence.Id;
import javax.persistence.Table;
import javax.validation.constraints.NotBlank;
import javax.validation.constraints.Size;

@Entity
@Table(name = "purchase_orders")
public class Order {
    @Id
    @Column(name = "order_id", nullable = false, updatable = false)
    private long id;

    @NotBlank(message = "{order.customer.required}")
    @Size(min = 2, max = 80)
    @Column(name = "customer_name", nullable = false, length = 80)
    private String customerName;

    protected Order() {}

    public Order(long id, String customerName) {
        this.id = id;
        this.customerName = customerName;
    }

    public long getId() {
        return id;
    }

    public String getCustomerName() {
        return customerName;
    }
}
