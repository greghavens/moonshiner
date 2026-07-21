package com.moonshiner.reports;

interface ReportCase {
    String name();

    void run() throws Exception;
}
