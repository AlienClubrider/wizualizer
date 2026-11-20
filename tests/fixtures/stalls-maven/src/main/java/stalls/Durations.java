package stalls;

/** Parses "90m" style durations. */
public final class Durations {
  private Durations() {}

  public static long minutes(String text) {
    if (!text.matches("\\d+m")) {
      throw new IllegalArgumentException("not a duration: " + text);
    }
    return Long.parseLong(text.substring(0, text.length() - 1));
  }
}
