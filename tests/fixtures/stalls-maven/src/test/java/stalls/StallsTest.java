package stalls;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.List;
import org.junit.jupiter.api.Test;

class StallsTest {
  @Test
  void groupsRoutesByPrefix() {
    RouteBook book = new RouteBook().add("/a/b").add("/a/c").add("/d");
    assertEquals(List.of("/a/b", "/a/c"), book.under("/a"));
  }

  @Test
  void parseThrowsUncaught() {
    Durations.minutes("ninety minutes");
  }

  @Test
  void countsEverything() {
    assertEquals(146, Census.count());
  }

  @Test
  void fallsBackToAScan() {
    assertEquals(2000, Lookups.find("k"));
  }

  @Test
  void spinsForever() throws InterruptedException {
    Spinner.spin();
  }
}
