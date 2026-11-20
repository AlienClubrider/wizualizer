package wiz;

import static wiz.JdkCollections.field;
import static wiz.JdkCollections.intField;
import static wiz.JdkCollections.longField;

import com.sun.jdi.ArrayReference;
import com.sun.jdi.ObjectReference;
import com.sun.jdi.PrimitiveValue;
import com.sun.jdi.StringReference;
import com.sun.jdi.Value;
import java.math.BigDecimal;
import java.math.BigInteger;
import java.net.URI;
import java.net.URISyntaxException;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.MonthDay;
import java.time.OffsetDateTime;
import java.time.OffsetTime;
import java.time.Period;
import java.time.Year;
import java.time.YearMonth;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.util.UUID;

/**
 * The text of a JDK value object (BigDecimal, BigInteger, UUID, URI, java.time), rebuilt in this
 * JVM from the object's fields so nothing runs in the test JVM. Null if the type is not one of
 * these or its fields are not as expected.
 */
final class JdkValues {
  private JdkValues() {}

  static String text(ObjectReference o) {
    try {
      Object local = mirror(o);
      return local == null ? null : local.toString();
    } catch (RuntimeException e) {
      return null;
    }
  }

  private static Object mirror(Value v) {
    if (!(v instanceof ObjectReference o)) return null;
    return switch (o.referenceType().name()) {
      case "java.math.BigInteger" -> bigInteger(o);
      case "java.math.BigDecimal" -> {
        long compact = longField(o, "intCompact");
        int scale = intField(o, "scale");
        if (compact != Long.MIN_VALUE) yield BigDecimal.valueOf(compact, scale);
        BigInteger unscaled = field(o, "intVal") instanceof ObjectReference bi ? bigInteger(bi) : null;
        yield unscaled == null ? null : new BigDecimal(unscaled, scale);
      }
      case "java.util.UUID" -> new UUID(longField(o, "mostSigBits"), longField(o, "leastSigBits"));
      case "java.net.URI" -> uri(o);
      case "java.time.LocalDate" -> LocalDate.of(intField(o, "year"), num(o, "month"), num(o, "day"));
      case "java.time.LocalTime" -> LocalTime.of(num(o, "hour"), num(o, "minute"), num(o, "second"), intField(o, "nano"));
      case "java.time.LocalDateTime" -> LocalDateTime.of((LocalDate) mirror(field(o, "date")), (LocalTime) mirror(field(o, "time")));
      case "java.time.Instant" -> Instant.ofEpochSecond(longField(o, "seconds"), intField(o, "nanos"));
      case "java.time.Duration" -> Duration.ofSeconds(longField(o, "seconds"), intField(o, "nanos"));
      case "java.time.Period" -> Period.of(intField(o, "years"), intField(o, "months"), intField(o, "days"));
      case "java.time.Year" -> Year.of(intField(o, "year"));
      case "java.time.YearMonth" -> YearMonth.of(intField(o, "year"), intField(o, "month"));
      case "java.time.MonthDay" -> MonthDay.of(intField(o, "month"), intField(o, "day"));
      case "java.time.ZoneOffset" -> ZoneOffset.ofTotalSeconds(intField(o, "totalSeconds"));
      case "java.time.ZoneRegion" -> ZoneId.of(str(o, "id"));
      case "java.time.OffsetDateTime" -> OffsetDateTime.of((LocalDateTime) mirror(field(o, "dateTime")), (ZoneOffset) mirror(field(o, "offset")));
      case "java.time.OffsetTime" -> OffsetTime.of((LocalTime) mirror(field(o, "time")), (ZoneOffset) mirror(field(o, "offset")));
      case "java.time.ZonedDateTime" -> ZonedDateTime.ofStrict(
          (LocalDateTime) mirror(field(o, "dateTime")), (ZoneOffset) mirror(field(o, "offset")), (ZoneId) mirror(field(o, "zone")));
      default -> null;
    };
  }

  private static Object uri(ObjectReference o) {
    if (field(o, "string") instanceof StringReference s) return s.value();
    try {
      return new URI(str(o, "scheme"), str(o, "authority"), str(o, "path"), str(o, "query"), str(o, "fragment"));
    } catch (URISyntaxException e) {
      return null;
    }
  }

  private static BigInteger bigInteger(ObjectReference o) {
    int signum = intField(o, "signum");
    if (!(field(o, "mag") instanceof ArrayReference mag)) return null;
    byte[] bytes = new byte[mag.length() * 4];
    for (int i = 0; i < mag.length(); i++) {
      int word = ((PrimitiveValue) mag.getValue(i)).intValue();
      for (int b = 0; b < 4; b++) bytes[i * 4 + b] = (byte) (word >>> (24 - 8 * b));
    }
    return new BigInteger(signum, bytes);
  }

  /** A byte or short field (java.time keeps month, day, hour... small). */
  private static int num(ObjectReference o, String name) {
    return field(o, name) instanceof PrimitiveValue p ? p.intValue() : 0;
  }

  private static String str(ObjectReference o, String name) {
    return field(o, name) instanceof StringReference s ? s.value() : null;
  }
}
