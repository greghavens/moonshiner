package com.acme.orders;

import java.io.IOException;
import javax.persistence.EntityManager;
import javax.persistence.PersistenceContext;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@WebServlet(name = "orderServlet", urlPatterns = "/orders", loadOnStartup = 1)
public final class OrderServlet extends HttpServlet {
    @PersistenceContext(unitName = "orders")
    private EntityManager entityManager;

    @Override
    protected void doPost(HttpServletRequest request, HttpServletResponse response)
            throws ServletException, IOException {
        long id = Long.parseLong(request.getParameter("id"));
        String customerName = request.getParameter("customer");
        entityManager.persist(new Order(id, customerName));
        response.setStatus(HttpServletResponse.SC_CREATED);
    }
}
