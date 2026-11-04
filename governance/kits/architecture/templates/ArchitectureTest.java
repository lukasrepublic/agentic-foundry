// ArchUnit (JUnit 5). Replace the package names. Researched 2026-11-04.
import com.tngtech.archunit.core.importer.ImportOption;
import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchRule;

import static com.tngtech.archunit.library.Architectures.layeredArchitecture;
import static com.tngtech.archunit.library.dependencies.SlicesRuleDefinition.slices;
import static com.tngtech.archunit.library.freeze.FreezingArchRule.freeze;

@AnalyzeClasses(packages = "com.example", importOptions = ImportOption.DoNotIncludeTests.class)
class ArchitectureTest {
    // freeze() stores known violations in a committed store, so only new violations fail.
    @ArchTest
    static final ArchRule layers = freeze(layeredArchitecture().consideringAllDependencies()
        .layer("Interface").definedBy("com.example.interfaces..")
        .layer("Application").definedBy("com.example.application..")
        .layer("Domain").definedBy("com.example.domain..")
        .whereLayer("Interface").mayNotBeAccessedByAnyLayer()
        .whereLayer("Application").mayOnlyBeAccessedByLayers("Interface")
        .whereLayer("Domain").mayOnlyBeAccessedByLayers("Application", "Interface"));

    @ArchTest
    static final ArchRule noCycles = slices().matching("com.example.(*)..").should().beFreeOfCycles();
}
