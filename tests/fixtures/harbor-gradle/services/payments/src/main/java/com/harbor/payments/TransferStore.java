package com.harbor.payments;

import java.math.BigDecimal;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;

@Repository
public class TransferStore {
  private final JdbcTemplate jdbc;

  public TransferStore(JdbcTemplate jdbc) {
    this.jdbc = jdbc;
  }

  public long save(String account, BigDecimal amount) {
    jdbc.update("insert into transfer (account, amount) values (?, ?)", account, amount);
    return jdbc.queryForObject("select max(id) from transfer", Long.class);
  }
}
