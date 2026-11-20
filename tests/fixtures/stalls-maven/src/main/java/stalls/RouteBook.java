package stalls;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Paths grouped by their first segment, like a web router's index. */
public final class RouteBook {
  private final Map<String, List<String>> byPrefix = new LinkedHashMap<>();

  public RouteBook add(String path) {
    byPrefix.computeIfAbsent(prefixOf(path), k -> new ArrayList<>(2)).add(path);
    return this;
  }

  private static String prefixOf(String path) {
    int cut = path.indexOf('/', 1);
    return cut < 0 ? path : path.substring(0, cut);
  }

  public List<String> under(String prefix) {
    return byPrefix.getOrDefault(prefix, List.of());
  }
}
