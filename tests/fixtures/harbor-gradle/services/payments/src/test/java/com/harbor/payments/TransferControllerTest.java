package com.harbor.payments;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

/** A service slice: the real context (validation, @Transactional, the aspect, an in-memory H2), the fraud API mocked. */
@SpringBootTest
@AutoConfigureMockMvc
class TransferControllerTest {
  @Autowired MockMvc mvc;
  @MockitoBean FraudCheckClient fraud;

  @Test
  void movesMoney() throws Exception {
    when(fraud.score(eq("ACC-1"), any())).thenReturn(10);
    mvc.perform(post("/transfers").contentType(MediaType.APPLICATION_JSON)
            .content("{\"account\": \"ACC-1\", \"amount\": 25.00}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.fraudScore").value(10));
  }

  @Test
  void rejectsABlankAccount() throws Exception {
    mvc.perform(post("/transfers").contentType(MediaType.APPLICATION_JSON)
            .content("{\"account\": \"\", \"amount\": 5}"))
        .andExpect(status().isBadRequest());
  }

  @Test
  void blocksARiskyTransfer() throws Exception {
    when(fraud.score(eq("ACC-9"), any())).thenReturn(95);
    mvc.perform(post("/transfers").contentType(MediaType.APPLICATION_JSON)
            .content("{\"account\": \"ACC-9\", \"amount\": 900}"))
        .andExpect(status().isConflict());
  }
}
