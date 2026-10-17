package org.miguelribeiro.mymusicgospel;

import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.provider.DocumentsContract;

import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;

/** SAF bridge for an atomic, user-owned save file in shared storage. */
public final class SaveFileBridge {
    private static final String SAVE_NAME = "MyMusicGospel.mmsave";
    private static final String PREVIOUS_NAME = "MyMusicGospel.previous.mmsave";
    private static final String MIME_TYPE = "application/zip";

    private SaveFileBridge() {}

    public static void keepPermission(Context context, String uriText, int flags) {
        Uri uri = Uri.parse(uriText);
        int access = flags & (Intent.FLAG_GRANT_READ_URI_PERMISSION
                | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        context.getContentResolver().takePersistableUriPermission(uri, access);
    }

    public static void saveToTree(Context context, String sourcePath, String treeUriText)
            throws Exception {
        ContentResolver resolver = context.getContentResolver();
        Uri treeUri = Uri.parse(treeUriText);
        String rootId = DocumentsContract.getTreeDocumentId(treeUri);
        Uri parent = DocumentsContract.buildDocumentUriUsingTree(treeUri, rootId);
        String temporaryName = "MyMusicGospel-" + System.currentTimeMillis() + ".tmp";
        Uri temporary = DocumentsContract.createDocument(
                resolver, parent, MIME_TYPE, temporaryName);
        if (temporary == null) throw new IllegalStateException("Não foi possível criar o arquivo temporário de save.");

        Uri oldSave = findChild(context, treeUri, SAVE_NAME);
        Uri oldPrevious = findChild(context, treeUri, PREVIOUS_NAME);
        Uri previous = null;
        boolean installed = false;
        try {
            try (InputStream input = new FileInputStream(sourcePath);
                 OutputStream output = resolver.openOutputStream(temporary, "wt")) {
                if (output == null) throw new IllegalStateException("Não foi possível escrever o arquivo de save.");
                copy(input, output);
                output.flush();
            }

            // The new archive is complete before the last good save is moved.
            if (oldSave != null) {
                if (oldPrevious != null && !DocumentsContract.deleteDocument(resolver, oldPrevious)) {
                    throw new IllegalStateException("Não foi possível preparar a troca segura do save.");
                }
                previous = DocumentsContract.renameDocument(resolver, oldSave, PREVIOUS_NAME);
                if (previous == null) throw new IllegalStateException("O local escolhido não permite substituir o arquivo de save.");
            }

            Uri current = DocumentsContract.renameDocument(resolver, temporary, SAVE_NAME);
            if (current == null) throw new IllegalStateException("Não foi possível finalizar o arquivo de save.");
            installed = true;
            if (previous != null) DocumentsContract.deleteDocument(resolver, previous);
            else if (oldPrevious != null) DocumentsContract.deleteDocument(resolver, oldPrevious);
        } catch (Exception error) {
            if (!installed && previous != null) {
                Uri current = findChild(context, treeUri, SAVE_NAME);
                if (current == null) DocumentsContract.renameDocument(resolver, previous, SAVE_NAME);
            }
            if (!installed) DocumentsContract.deleteDocument(resolver, temporary);
            throw error;
        }
    }

    public static void copyLatestFromTree(Context context, String treeUriText, String destinationPath)
            throws Exception {
        Uri treeUri = Uri.parse(treeUriText);
        Uri source = findChild(context, treeUri, SAVE_NAME);
        if (source == null) source = findChild(context, treeUri, PREVIOUS_NAME);
        if (source == null) throw new IllegalStateException("Não encontrei um save nessa pasta.");

        ContentResolver resolver = context.getContentResolver();
        try (InputStream input = resolver.openInputStream(source);
             OutputStream output = new FileOutputStream(destinationPath)) {
            if (input == null) throw new IllegalStateException("Não foi possível ler o arquivo de save.");
            copy(input, output);
            output.flush();
            ((FileOutputStream) output).getFD().sync();
        }
    }

    private static Uri findChild(Context context, Uri treeUri, String displayName) {
        ContentResolver resolver = context.getContentResolver();
        String parentId = DocumentsContract.getTreeDocumentId(treeUri);
        Uri children = DocumentsContract.buildChildDocumentsUriUsingTree(treeUri, parentId);
        String[] projection = {DocumentsContract.Document.COLUMN_DOCUMENT_ID,
                DocumentsContract.Document.COLUMN_DISPLAY_NAME};
        try (Cursor cursor = resolver.query(children, projection, null, null, null)) {
            if (cursor == null) return null;
            int idIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DOCUMENT_ID);
            int nameIndex = cursor.getColumnIndexOrThrow(
                    DocumentsContract.Document.COLUMN_DISPLAY_NAME);
            while (cursor.moveToNext()) {
                if (displayName.equals(cursor.getString(nameIndex))) {
                    return DocumentsContract.buildDocumentUriUsingTree(
                            treeUri, cursor.getString(idIndex));
                }
            }
        }
        return null;
    }

    private static void copy(InputStream input, OutputStream output) throws Exception {
        byte[] buffer = new byte[64 * 1024];
        int count;
        while ((count = input.read(buffer)) != -1) {
            output.write(buffer, 0, count);
        }
    }
}
