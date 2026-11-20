package stalls;

/** Never returns: stands in for a test that hangs or crawls under the recorder. */
public final class Spinner {
  private Spinner() {}

  public static long spin() throws InterruptedException {
    long turns = 0;
    while (true) {
      Thread.sleep(200);
      turns++;
    }
  }
}
