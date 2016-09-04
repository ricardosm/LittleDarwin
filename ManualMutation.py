import re
import os
import unicodedata
import fnmatch
import shelve
import sys
from Levenshtein import _levenshtein

class CodeFile(object):
    def __init__(self, path = None, fullPath = None):
        self.path = path
        self.fullPath = fullPath
        self.contents = None
        self.strippedContents = None
        self.distanceFrom = dict()

    def stripJavaCode(self, javaCode):
        assert isinstance(javaCode, str)

        pattern = r"""
                                ##  --------- COMMENT ---------
               /\*              ##  Start of /* ... */ comment
               [^*]*\*+         ##  Non-* followed by 1-or-more *'s
               (                ##
                 [^/*][^*]*\*+  ##
               )*               ##  0-or-more things which don't start with /
                                ##    but do end with '*'
               /                ##  End of /* ... */ comment
             |                  ##  -OR-  various things which aren't comments:
               (                ##
                                ##  ------ " ... " STRING ------
                 "              ##  Start of " ... " string
                 (              ##
                   \\.          ##  Escaped char
                 |              ##  -OR-
                   [^"\\]       ##  Non "\ characters
                 )*             ##
                 "              ##  End of " ... " string
               |                ##  -OR-
                                ##
                                ##  ------ ' ... ' STRING ------
                 '              ##  Start of ' ... ' string
                 (              ##
                   \\.          ##  Escaped char
                 |              ##  -OR-
                   [^'\\]       ##  Non '\ characters
                 )*             ##
                 '              ##  End of ' ... ' string
               |                ##  -OR-
                                ##
                                ##  ------ ANYTHING ELSE -------
                 .              ##  Anything other char
                 [^/"'\\]*      ##  Chars which doesn't start a comment, string
               )                ##    or escape
        """
        regex = re.compile(pattern, re.VERBOSE | re.MULTILINE | re.DOTALL)
        noncomments = [m.group(2) for m in regex.finditer(javaCode) if m.group(2)]

        phase1 = "".join(noncomments)
        phase2 = re.sub('[\s+]', '', phase1)

        return phase2

    def readContents(self):
        assert os.path.exists(self.fullPath)
        with open(self.fullPath, mode='r', errors='replace') as contentFile:
            fileData = contentFile.read()
        self.contents = unicodedata.normalize('NFKD', fileData).encode('ascii', 'replace')

    def __sub__(self, other):
        assert isinstance(other, type(self))
        if self.strippedContents is None:
            self.strippedContents = self.stripJavaCode(self.contents)

        if other.strippedContents is None:
            other.strippedContents = other.stripJavaCode(other.contents)

        if abs(len(self.strippedContents) - len(other.strippedContents)) > max(0.1 * (len(self.strippedContents) + len(other.strippedContents)), 50):
            return 99999

        return _levenshtein.distance(self.strippedContents, other.strippedContents)


class MutantFile(CodeFile):
    def __init__(self, path, fullPath):
        CodeFile.__init__(path, fullPath)

    def calculateDistance(self, sourceFile):
        assert isinstance(sourceFile, SourceFile)
        self.distanceFrom[sourceFile] = sourceFile - self

class SourceFile(CodeFile):
    def __init__(self, path, fullPath):
        CodeFile.__init__(path, fullPath)
        self.associatedMutantFiles = list()

class ManualMutation(object):
    def __init__(self, sourcePath = None, mutantsPath = None):
        self.sourcePath = sourcePath
        self.mutantsPath = mutantsPath
        self.targetPath = None
        self.sourceFiles = list()
        self.mutantFiles = list()
        self.targetPath = os.path.abspath(os.path.join(self.sourcePath, os.path.pardir, "mutated"))
        self.databaseHandle = None

    def listFiles(self, target_path=None, desired_type="*.java"):
        fileList = list()
        for root, dirnames, filenames in os.walk(target_path):
            for filename in fnmatch.filter(filenames, desired_type):
                fileList.append(os.path.join(root, filename))

        return fileList

    def initialize(self):
        assert os.path.isdir(self.sourcePath)
        assert os.path.isdir(self.mutantsPath)

        sourceFilePaths = self.listFiles(self.sourcePath)
        for sourceFilePath in sourceFilePaths:
            tmpRelativePath = os.path.relpath(sourceFilePath, self.sourcePath)
            tmpSourceFileObj = SourceFile(tmpRelativePath, sourceFilePath)
            tmpSourceFileObj.readContents()
            self.sourceFiles.append(tmpSourceFileObj)
            sys.stdout.write(str(len(self.sourceFiles)) + " source files found.   \r")

        mutantFilePaths = self.listFiles(self.mutantsPath)
        for mutantFilePath in mutantFilePaths:
            tmpRelativePath = os.path.relpath(mutantFilePath, self.mutantsPath)
            tmpMutantFileObj = MutantFile(tmpRelativePath, mutantFilePath)
            tmpMutantFileObj.readContents()
            self.mutantFiles.append(tmpMutantFileObj)
            sys.stdout.write(str(len(self.mutantFiles)) + " mutants found.   \r")

    def groupMutants(self):
        print("\nCalculating distances:")
        totalOperations = len(self.sourceFiles) * len(self.mutantFiles)
        i = 0

        for sourceFile in self.sourceFiles:
            assert isinstance(sourceFile, SourceFile)
            for mutantFile in self.mutantFiles:
                assert isinstance(mutantFile, MutantFile)
                mutantFile.calculateDistance(sourceFile)
                sourceFile.distanceFrom[mutantFile] = mutantFile.distanceFrom[sourceFile]
                i += 1
                sys.stdout.write(str(i) + "/" + str(totalOperations) + " ({0:.2f}%)".format((100.0*i)/totalOperations) + "       \r")
                sys.stdout.flush()

        for mutantFile in self.mutantFiles:
            assert isinstance(mutantFile, MutantFile)
            tmpMin = None
            for sourceFile in mutantFile.distanceFrom.keys():
                tmpMin = sourceFile if tmpMin is None or mutantFile.distanceFrom[sourceFile] < mutantFile.distanceFrom[tmpMin] else tmpMin

            assert isinstance(tmpMin, SourceFile)
            tmpMin.associatedMutantFiles.append(mutantFile)

        # for sourceFile in self.sourceFiles:
        #     assert isinstance(sourceFile, SourceFile)
        #     validValues = [ i for i in sourceFile.distanceFrom.values() if i < 99999 ]
        #     meanValue = sum(validValues) / len(validValues)
        #
        #     for mutantFile in self.mutantFiles:
        #         assert isinstance(mutantFile, MutantFile)
        #         if sourceFile.distanceFrom[mutantFile] < max(150, 0.3 * meanValue):
        #             sourceFile.associatedMutantFiles.append(mutantFile)

    def createMutationStructure(self):
        self.databasePath = os.path.join(self.targetPath, "manualmutationdatabase")
        self.databaseHandle = shelve.open(self.databasePath, "c")

        if not os.path.exists(self.targetPath):
            os.makedirs(self.targetPath)











