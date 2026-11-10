plugins {
    id("harbor.spring-service")
}

dependencies {
    implementation(project(":common:ledger"))
}
