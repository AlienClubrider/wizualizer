package com.harbor.payments;

import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;

@SpringBootTest
@AutoConfigureMockMvc
class QuoteControllerTest {
  @Autowired MockMvc mvc;
  @MockitoBean FxRateClient rates;

  @Test
  void quotesAnInternationalTransfer() throws Exception {
    when(rates.rate("USD", "EUR")).thenReturn(new BigDecimal("0.92"));
    mvc.perform(post("/quotes").contentType(MediaType.APPLICATION_JSON)
            .content("{\"amount\": 1000.00, \"from\": \"USD\", \"to\": \"EUR\"}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.fee").value(15.00))
        .andExpect(jsonPath("$.receives").value(906.20));
  }

  @Test
  void smallDomesticTransfersAreFree() throws Exception {
    mvc.perform(post("/quotes").contentType(MediaType.APPLICATION_JSON)
            .content("{\"amount\": 20.00, \"from\": \"USD\", \"to\": \"USD\"}"))
        .andExpect(status().isOk())
        .andExpect(jsonPath("$.fee").value(0))
        .andExpect(jsonPath("$.receives").value(20.00));
  }

  @Test
  void rejectsANegativeAmount() throws Exception {
    mvc.perform(post("/quotes").contentType(MediaType.APPLICATION_JSON)
            .content("{\"amount\": -5, \"from\": \"USD\", \"to\": \"EUR\"}"))
        .andExpect(status().isBadRequest());
  }
}
